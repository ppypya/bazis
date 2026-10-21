import asyncio

from config.worker import create_worker


def main() -> None:
    asyncio.run(create_worker().run())


if __name__ == "__main__":
    main()
