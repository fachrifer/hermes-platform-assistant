from __future__ import annotations

import os
from pathlib import Path

from office_gateway.tools.core import Param, Tool, ToolError

# All office agents can write to the shared reports folder.
ALLOWED = frozenset({"supervisor", "lab-host", "llm", "ingress", "cluster-gpu", "vector", "obs"})

CANDIDATES = [
    Path("/reports"),
    Path("/opt/data/reports"),
    Path("/var/lib/hermes-office-gateway/reports"),
    Path("/tmp/hermes-reports"),
]


async def _athena_write_report(ctx, args, role):
    filename = (args.get("filename") or "").strip()
    content = args.get("content") or ""
    file_type = (args.get("file_type") or "html").strip().lower()

    if file_type not in ("html", "txt", "json", "csv", "md"):
        raise ToolError("invalid_argument", f"file_type must be html, txt, json, csv, or md (got '{file_type}')")

    if not filename:
        raise ToolError("invalid_argument", "filename is required")

    if not content:
        raise ToolError("invalid_argument", "content is required and cannot be empty")

    safe_name = os.path.basename(filename)
    if not safe_name or safe_name.startswith("."):
        raise ToolError("invalid_argument", "invalid filename (no path separators, no dotfiles)")

    if "." not in safe_name:
        safe_name = f"{safe_name}.{file_type}"
    elif not safe_name.endswith(f".{file_type}"):
        safe_name = f"{safe_name}.{file_type}"

    last_error = None
    for directory in CANDIDATES:
        try:
            directory.mkdir(parents=True, exist_ok=True)
            target = directory / safe_name
            target.write_text(content, encoding="utf-8")
            shared = str(directory) in ("/reports", "/opt/data/reports")
            return {
                "ok": True,
                "path": str(target),
                "size_bytes": target.stat().st_size,
                "shared_with_vm": shared,
                "message": (
                    f"Report written to {target} — visible on VM at /home/timai/hermes-assistant/reports/{safe_name}"
                    if shared
                    else f"Report written to {target} (FALLBACK — not the shared VM folder)"
                ),
            }
        except (PermissionError, OSError) as exc:
            last_error = exc
            continue

    raise ToolError("unreachable", f"write_report: no writable directory found: {last_error}")


TOOLS = (
    Tool(
        "athena_write_report",
        ALLOWED,
        "Write a report file (html/txt/json/csv/md) to the shared reports folder (/reports, mapped to /home/timai/hermes-assistant/reports on the VM). Available to all specialists.",
        {
            "filename": Param("string", "output filename (e.g. 'litellm-spend.html')", required=True),
            "content": Param("string", "full file content to write", required=True),
            "file_type": Param("string", "file extension: html, txt, json, csv, md", default="html"),
        },
        _athena_write_report,
    ),
)