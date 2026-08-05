"""CML Application entry for Qwen3 8B.

Target:
  Project  : qwen3_8b
  Runtime  : PBJ Workbench · Python 3.12 · Nvidia GPU
  Script   : /home/cdsw/run_app.py  (dedicated project root)

Also supports nested layout: /home/cdsw/qwen3_8b/run_app.py

CML may execute this file in a notebook-like runtime (running asyncio loop,
no __file__). Modules load by absolute path; uvicorn starts in a side thread.
"""

from __future__ import annotations

import asyncio
import importlib.util
import logging
import os
import sys
import threading
from pathlib import Path
from types import ModuleType

import uvicorn

logging.basicConfig(level=logging.INFO)


def _candidate_dirs() -> list[Path]:
    """Prefer dedicated project root (/home/cdsw), then nested folder."""
    dirs: list[Path] = []
    env_dir = os.environ.get("QWEN_APP_DIR", "").strip()
    if env_dir:
        dirs.append(Path(env_dir))
    # Dedicated CML project named qwen3_8b → files live at project root.
    dirs.append(Path("/home/cdsw"))
    dirs.append(Path("/home/cdsw/qwen3_8b"))
    try:
        dirs.append(Path(__file__).resolve().parent)
    except NameError:
        pass
    if sys.argv and sys.argv[0]:
        argv = Path(sys.argv[0]).resolve()
        dirs.append(argv.parent if argv.is_file() else argv)
    seen: set[str] = set()
    out: list[Path] = []
    for path in dirs:
        key = str(path)
        if key not in seen:
            seen.add(key)
            out.append(path)
    return out


def _find_app_dir() -> Path:
    for path in _candidate_dirs():
        if (path / "engine.py").is_file() and (path / "serve.py").is_file():
            return path
    tried = ", ".join(str(p) for p in _candidate_dirs())
    raise RuntimeError(
        "Cannot find engine.py/serve.py. For project qwen3_8b upload files to "
        f"/home/cdsw/ (or /home/cdsw/qwen3_8b/) or set QWEN_APP_DIR. Tried: {tried}"
    )


def _check_python() -> None:
    if sys.version_info[:2] != (3, 12):
        logging.warning(
            "Recommended runtime is PBJ Workbench Python 3.12 (got %s). "
            "Continue anyway, but pin the Application runtime to Python 3.12.",
            sys.version.split()[0],
        )


def _load_module(module_name: str, file_path: Path) -> ModuleType:
    spec = importlib.util.spec_from_file_location(module_name, file_path)
    if spec is None or spec.loader is None:
        raise ImportError(f"Cannot load {file_path}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[module_name] = module
    spec.loader.exec_module(module)
    return module


def _has_running_loop() -> bool:
    try:
        asyncio.get_running_loop()
        return True
    except RuntimeError:
        return False


def _start_uvicorn(app: object, host: str, port: int, *, blocking: bool = True) -> threading.Thread | None:
    """Start uvicorn. If blocking=False, return the server thread (daemon)."""
    config = uvicorn.Config(app, host=host, port=port, log_level="info")
    server = uvicorn.Server(config)

    def _run() -> None:
        if _has_running_loop():
            asyncio.run(server.serve())
        else:
            server.run()

    if blocking and not _has_running_loop():
        server.run()
        return None

    # CML / preload flow: run server in a dedicated thread.
    thread = threading.Thread(target=_run, name="qwen-uvicorn", daemon=not blocking)
    thread.start()
    if blocking:
        thread.join()
        return None
    # Give the server a moment to bind CDSW_APP_PORT before heavy work.
    import time

    time.sleep(1.5)
    return thread


def main() -> None:
    _check_python()
    app_dir = _find_app_dir()
    logging.info(
        "Qwen app_dir=%s python=%s (project=qwen3_8b, runtime=PBJ Workbench 3.12)",
        app_dir,
        sys.version.split()[0],
    )

    engine_mod = _load_module("qwen3_8b_engine", app_dir / "engine.py")
    serve_mod = _load_module("qwen3_8b_serve", app_dir / "serve.py")

    engine = engine_mod.TransformersEngine()
    app = serve_mod.create_app(engine)
    host = os.environ.get("QWEN_HOST", "127.0.0.1").strip() or "127.0.0.1"
    port = int(os.environ.get("CDSW_APP_PORT") or os.environ.get("PORT") or "8080")
    on_cml = bool(os.environ.get("CDSW_APP_PORT"))

    # QWEN_PRELOAD:
    #   1 / true  = bind HTTP first (CML-safe), then BLOCKING load so logs show
    #               success/failure clearly; process stays up on failure.
    #   background = bind HTTP, load in background thread
    #   0 = lazy on first chat
    preload_mode = os.environ.get("QWEN_PRELOAD", "1").strip().lower() or "1"

    logging.info(
        "Starting Qwen HTTP on %s:%s (model_path=%s preload=%s)",
        host,
        port,
        engine.model_path,
        preload_mode,
    )

    if preload_mode in {"0", "false", "no", "off"}:
        logging.info("QWEN_PRELOAD=%s — lazy load on first chat", preload_mode)
        _start_uvicorn(app, host, port, blocking=True)
        return

    if preload_mode in {"background", "async"}:
        def _bg_preload() -> None:
            try:
                logging.info("Background preload starting…")
                engine.preload()
                logging.info("Background model preload finished OK")
            except Exception as exc:  # noqa: BLE001
                logging.exception("Background model preload FAILED: %s", exc)

        threading.Thread(target=_bg_preload, name="qwen-preload", daemon=True).start()
        _start_uvicorn(app, host, port, blocking=True)
        return

    # Default QWEN_PRELOAD=1: keep loading visible/blocking, but on CML bind
    # the port first so the Application is not killed mid-load.
    if on_cml:
        logging.info(
            "QWEN_PRELOAD=1 — binding CDSW_APP_PORT first, then blocking model load "
            "(watch logs /health for ok|error)"
        )
        server_thread = _start_uvicorn(app, host, port, blocking=False)
        try:
            logging.info("Loading weights (blocking) from %s …", engine.model_path)
            engine.preload()
            logging.info("MODEL LOAD OK — ready for /v1/chat/completions")
        except Exception as exc:  # noqa: BLE001
            logging.exception("MODEL LOAD FAILED — server stays up for diagnosis: %s", exc)
            logging.error(
                "Check GET /health for status=error. Fix torch/GPU/model, then Restart Application."
            )
        if server_thread is not None:
            server_thread.join()
        return

    logging.info("QWEN_PRELOAD=1 — loading weights BEFORE serving")
    engine.preload()
    logging.info("Model ready — starting HTTP server")
    _start_uvicorn(app, host, port, blocking=True)


if __name__ == "__main__" or os.environ.get("CDSW_APP_PORT"):
    main()
