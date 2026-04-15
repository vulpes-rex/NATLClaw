import asyncio
import logging
import sys

from config import load_config, validate_config
from scheduler import run_scheduler
from telemetry import init_sentry


def main() -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
        stream=sys.stdout,
    )

    config = load_config()
    init_sentry(config)

    errors = validate_config(config)
    if errors:
        for err in errors:
            print(f"Config error: {err}", file=sys.stderr)
        sys.exit(1)

    try:
        asyncio.run(run_scheduler(config))
    except KeyboardInterrupt:
        logging.getLogger(__name__).info("Shutting down (Ctrl+C).")


if __name__ == "__main__":
    main()
