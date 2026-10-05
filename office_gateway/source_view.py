"""Read-only view of gateway Python and scripts. Secrets and parent paths stay closed."""

from __future__ import annotations

import os
import re
from pathlib import Path

_SUFFIXES = {".py", ".sh", ".md", ".yml", ".yaml"}
_LIMIT = 12000
_SCRIPT_FILE = re.compile(r"^[a-z][a-z0-9_-]{0,40}\.(sh|py)$")


def gateway_roots() -> dict[str, Path]:
    return {
        "office_gateway": Path(os.environ.get("OFFICE_GATEWAY_SOURCE", "/app/office_gateway")),
        "scripts": Path(os.environ.get("OFFICE_GATEWAY_SCRIPTS", "/gateway_scripts")),
    }


def read_source(roots: dict[str, Path], relpath: str, limit: int = _LIMIT) -> dict:
    rel = (relpath or "").strip().replace("\\", "/").lstrip("/")
    parts = [part for part in rel.split("/") if part]
    if not parts or any(part in {".", ".."} for part in parts):
        raise ValueError("path is invalid")
    root = roots.get(parts[0])
    if root is None:
        raise ValueError("path must start with office_gateway/ or scripts/")
    candidate = root.joinpath(*parts[1:])
    if not root.exists():
        raise ValueError(f"{parts[0]} is not mounted")
    try:
        resolved = candidate.resolve()
        root_resolved = root.resolve()
    except OSError as exc:
        raise ValueError("path is invalid") from exc
    if root_resolved != resolved and root_resolved not in resolved.parents:
        raise ValueError("path is invalid")
    if resolved.suffix not in _SUFFIXES or resolved.name.startswith(".env"):
        raise ValueError("path is not a readable script")
    if not resolved.is_file():
        raise ValueError("path was not found")
    text = resolved.read_text(encoding="utf-8", errors="replace")
    return {"path": rel, "text": text[:limit], "truncated": len(text) > limit}


def script_destination(roots: dict[str, Path], relpath: str, content: str, replace: bool) -> Path:
    rel = (relpath or "").strip().replace("\\", "/").lstrip("/")
    parts = [part for part in rel.split("/") if part]
    if len(parts) != 2 or parts[0] != "scripts" or not _SCRIPT_FILE.fullmatch(parts[1]):
        raise ValueError("path must be scripts/<name>.sh or scripts/<name>.py")
    if not content or not content.strip():
        raise ValueError("script text is required")
    if len(content) > _LIMIT or "\x00" in content:
        raise ValueError("script text is too long")
    root = roots.get("scripts")
    if root is None or not root.exists():
        raise ValueError("scripts is not mounted")
    path = (root / parts[1]).resolve()
    if path.parent != root.resolve():
        raise ValueError("path is invalid")
    if path.exists() and not replace:
        raise ValueError("that script already exists")
    return path


def write_script(roots: dict[str, Path], relpath: str, content: str, replace: bool) -> dict:
    path = script_destination(roots, relpath, content, replace)
    if not content.endswith("\n"):
        content += "\n"
    temporary = path.with_name(f".{path.name}.writing")
    temporary.write_text(content, encoding="utf-8", newline="\n")
    temporary.chmod(0o755 if path.suffix == ".sh" else 0o644)
    temporary.replace(path)
    return {"path": relpath.strip().replace("\\", "/").lstrip("/"), "bytes": len(content.encode("utf-8"))}
