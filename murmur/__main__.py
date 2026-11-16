"""`python -m murmur` — start the local server."""
from __future__ import annotations

import uvicorn

from . import config, netinfo, runtime


def main() -> None:
    host = runtime.resolve_host()
    runtime.apply(host, config.PORT)

    if config.PUBLIC:
        print(f"Listening on {host}:{config.PORT}")
        for item in netinfo.access_urls(config.PORT, config.access_token()):
            if item["label"] != "This Mac":
                print(f"  {item['label']}: {item['url']}")

    uvicorn.run(
        "murmur.server:app",
        host=host,
        port=config.PORT,
        log_level="warning",
        access_log=False,
    )


if __name__ == "__main__":
    main()
