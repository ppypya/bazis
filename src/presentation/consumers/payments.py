import asyncio
import logging
from uuid import UUID

from faststream import AckPolicy
from faststream.rabbit import RabbitBroker, RabbitExchange, RabbitMessage, RabbitQueue
from pydantic import BaseModel, Field, ValidationError

from application.exceptions import LostLease, PaymentNotFound, ProcessingBusy
from application.use_cases.process_payment import ProcessPayment

logger = logging.getLogger(__name__)


class PaymentMessage(BaseModel):
    event_id: UUID
    payment_id: UUID
    attempt: int = Field(ge=1, le=3)


def raw_decoder(message: RabbitMessage) -> bytes:
    # Parse in the handler so malformed JSON also receives an explicit rejection.
    return message.body


def register_consumer(
    broker: RabbitBroker, queue: RabbitQueue, exchange: RabbitExchange, use_case: ProcessPayment
) -> None:
    @broker.subscriber(
        queue,
        exchange,
        ack_policy=AckPolicy.MANUAL,
        no_reply=True,
        decoder=raw_decoder,
    )
    async def handle_payment(message: RabbitMessage) -> None:
        try:
            body = PaymentMessage.model_validate_json(message.body)
        except (ValidationError, ValueError, TypeError):
            logger.warning("Invalid message sent to DLQ")
            await message.reject(requeue=False)
            return

        try:
            await use_case.execute(body.payment_id, body.attempt)
        except (PaymentNotFound, ValueError):
            logger.exception("Invalid payment command payment_id=%s", body.payment_id)
            await message.reject(requeue=False)
        except (ProcessingBusy, LostLease):
            await asyncio.sleep(1)
            await message.nack(requeue=True)
        except Exception:
            # No ACK until the DB contains either completion or a retry/DLQ event.
            logger.exception("Processing unavailable payment_id=%s", body.payment_id)
            await asyncio.sleep(1)
            await message.nack(requeue=True)
        else:
            logger.info("Attempt stored payment_id=%s attempt=%s", body.payment_id, body.attempt)
            await message.ack()
