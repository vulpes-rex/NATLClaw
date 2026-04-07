import asyncio
import logging
import sys

from config import load_config
from scheduler import run_scheduler


def main() -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
        stream=sys.stdout,
    )

    config = load_config()

    try:
        asyncio.run(run_scheduler(config))
    except KeyboardInterrupt:
        logging.getLogger(__name__).info("Shutting down (Ctrl+C).")


if __name__ == "__main__":
    main()
