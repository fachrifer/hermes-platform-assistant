"""CML Application entry for Qwen3 8B."""

from __future__ import annotations

import logging
import os

import uvicorn

from cml.qwen3_8b.engine import TransformersEngine
from cml.qwen3_8b.serve import create_app

logging.basicConfig(level=logging.INFO)


def main() -> None:
    engine = TransformersEngine()
    app = create_app(engine)
    port = int(os.environ.get("CDSW_APP_PORT") or os.environ.get("PORT") or "8080")
    uvicorn.run(app, host="127.0.0.1", port=port, log_level="info")


if __name__ == "__main__":
    main()
