import asyncio
import hashlib
import random

from domain.entities.payment import Payment, PaymentStatus


class EmulatedPaymentGateway:
    def __init__(self, min_delay: float = 2, max_delay: float = 5) -> None:
        self.min_delay = min_delay
        self.max_delay = max_delay

    async def charge(self, payment: Payment) -> PaymentStatus:
        await asyncio.sleep(random.uniform(self.min_delay, self.max_delay))

        # Stable across retries/restarts, including a crash before saving the result.
        sample = int.from_bytes(hashlib.sha256(payment.id.bytes).digest()[:8], "big")
        return PaymentStatus.SUCCEEDED if sample % 10 != 0 else PaymentStatus.FAILED
