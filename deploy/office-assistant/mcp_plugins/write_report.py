from __future__ import annotations

import os
from pathlib import Path

from office_gateway.tools.core import Param, Tool, ToolError

REPORTS_DIR = Path("/var/lib/hermes-office-gateway/reports")

ALLOWED = frozenset({"llm"})


async def _write_report(ctx, args, role):
    filename = (args.get("filename") or "").strip()
    content = args.get("content") or ""
    file_type = (args.get("file_type") or "html").strip().lower()

    if file_type not in ("html", "txt", "json", "csv"):
        raise ToolError("invalid_argument", f"file_type must be html, txt, json, or csv (got '{file_type}')")

    if not filename:
        raise ToolError("invalid_argument", "filename is required")

    safe_name = os.path.basename(filename)
    if not safe_name or safe_name.startswith("."):
        raise ToolError("invalid_argument", "invalid filename")

    if "." not in safe_name:
        safe_name = f"{safe_name}.{file_type}"
    elif not safe_name.endswith(f".{file_type}"):
        safe_name = f"{safe_name}.{file_type}"

    try:
        REPORTS_DIR.mkdir(parents=True, exist_ok=True)
        target = REPORTS_DIR / safe_name
        target.write_text(content, encoding="utf-8")
    except PermissionError:
        # Fallback: try /tmp (always writable)
        fallback_dir = Path("/tmp/hermes-reports")
        fallback_dir.mkdir(parents=True, exist_ok=True)
        target = fallback_dir / safe_name
        target.write_text(content, encoding="utf-8")
        return {
            "ok": True,
            "path": str(target),
            "size_bytes": target.stat().st_size,
            "message": f"Report written to {target} (fallback: /opt/data not writable)",
        }

    return {
        "ok": True,
        "path": str(target),
        "size_bytes": target.stat().st_size,
        "message": f"Report written to {target}",
    }


TOOLS = (
    Tool(
        "write_report",
        ALLOWED,
        "Write a report file (html/txt/json/csv) to the gateway reports directory.",
        {
            "filename": Param("string", "output filename (e.g. 'litellm-spend.html')", required=True),
            "content": Param("string", "full file content to write", required=True),
            "file_type": Param("string", "file extension: html, txt, json, csv", default="html"),
        },
        _write_report,
    ),
)