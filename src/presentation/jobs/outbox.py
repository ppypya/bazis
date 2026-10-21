import asyncio
import logging

from application.use_cases.publish_outbox import PublishOutbox

logger = logging.getLogger(__name__)


async def run_outbox(publisher: PublishOutbox, poll_seconds: float) -> None:
    while True:
        try:
            published = await publisher.execute()
        except Exception:
            logger.exception("Outbox temporarily unavailable")
            published = False

        if not published:
            await asyncio.sleep(poll_seconds)
