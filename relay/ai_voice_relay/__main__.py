"""Run with: python -m ai_voice_relay"""

import asyncio
import logging
import os
import sys

from .pipeline import ClaudePipeline
from .server import serve_forever


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    token = os.environ.get("RELAY_TOKEN", "")
    if len(token) < 16:
        sys.exit("Set RELAY_TOKEN to a random string of at least 16 characters.")
    if not os.environ.get("ANTHROPIC_API_KEY"):
        sys.exit("Set ANTHROPIC_API_KEY (or run `ant auth login`).")
    host = os.environ.get("RELAY_HOST", "0.0.0.0")
    port = int(os.environ.get("RELAY_PORT", "8765"))
    asyncio.run(serve_forever(host, port, token, ClaudePipeline()))


if __name__ == "__main__":
    main()
