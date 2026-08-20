from __future__ import annotations

import uvicorn

from office_gateway.app import create_app
from office_gateway.config import GatewayConfig


def main() -> None:
    config = GatewayConfig.from_env()
    app = create_app(config)
    uvicorn.run(app, host=config.bind_host, port=config.bind_port)


if __name__ == "__main__":
    main()
