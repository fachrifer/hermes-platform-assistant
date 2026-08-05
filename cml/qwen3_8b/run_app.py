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


def _start_uvicorn(app: object, host: str, port: int) -> None:
    """Start uvicorn; safe when CML already has an asyncio event loop."""
    config = uvicorn.Config(app, host=host, port=port, log_level="info")
    server = uvicorn.Server(config)

    if not _has_running_loop():
        server.run()
        return

    # CML notebook-style Application: main thread already has a loop.
    # Run uvicorn in a dedicated thread with its own event loop, then block.
    done = threading.Event()
    error: list[BaseException] = []

    def _in_thread() -> None:
        try:
            asyncio.run(server.serve())
        except BaseException as exc:  # noqa: BLE001
            error.append(exc)
        finally:
            done.set()

    thread = threading.Thread(target=_in_thread, name="qwen-uvicorn", daemon=False)
    thread.start()
    thread.join()
    if error:
        raise error[0]


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

    # Default: load weights BEFORE uvicorn so chat does not hit cold-start 502.
    # Set QWEN_PRELOAD=0 to skip (lazy load on first chat).
    # Set QWEN_PRELOAD=background to bind port first, then load in a thread.
    preload_mode = os.environ.get("QWEN_PRELOAD", "1").strip().lower() or "1"
    if preload_mode in {"0", "false", "no", "off"}:
        logging.info("QWEN_PRELOAD=%s — lazy load on first chat", preload_mode)
    elif preload_mode in {"background", "async"}:
        logging.info("QWEN_PRELOAD=background — loading weights after server bind")

        def _bg_preload() -> None:
            try:
                engine.preload()
            except Exception:  # noqa: BLE001
                logging.exception("Background model preload failed")

        threading.Thread(target=_bg_preload, name="qwen-preload", daemon=True).start()
    else:
        logging.info("QWEN_PRELOAD=1 — loading weights BEFORE serving (may take several minutes)")
        engine.preload()
        logging.info("Model ready — starting HTTP server")

    logging.info(
        "Starting Qwen on %s:%s (model_id=%s path=%s loaded=%s)",
        host,
        port,
        engine.model_id,
        engine.model_path,
        getattr(engine, "is_loaded", False),
    )
    _start_uvicorn(app, host, port)


if __name__ == "__main__" or os.environ.get("CDSW_APP_PORT"):
    main()
