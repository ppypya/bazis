import asyncio
import signal

from config.outbox import run_outbox_worker


async def run() -> None:
    loop = asyncio.get_running_loop()
    task = asyncio.current_task()
    assert task is not None
    stopping = False

    def stop() -> None:
        nonlocal stopping
        if not stopping:
            stopping = True
            task.cancel()

    signals = (signal.SIGINT, signal.SIGTERM)
    previous_handlers = {signum: signal.getsignal(signum) for signum in signals}
    for signum in signals:
        loop.add_signal_handler(signum, stop)
    try:
        await run_outbox_worker()
    except asyncio.CancelledError:
        # Only an explicit shutdown signal makes cancellation a normal exit.
        if not stopping:
            raise
    finally:
        for signum in signals:
            loop.remove_signal_handler(signum)
            signal.signal(signum, previous_handlers[signum])


def main() -> None:
    asyncio.run(run())


if __name__ == "__main__":
    main()
