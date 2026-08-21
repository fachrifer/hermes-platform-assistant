from __future__ import annotations

import sys

import uvicorn

from office_gateway.app import create_app
from office_gateway.config import GatewayConfig


def main() -> None:
    try:
        config = GatewayConfig.from_env()
    except ValueError as exc:
        print(f"office-gateway config error: {exc}", file=sys.stderr)
        raise SystemExit(1) from exc
    app = create_app(config)
    uvicorn.run(app, host=config.bind_host, port=config.bind_port)


if __name__ == "__main__":
    main()
