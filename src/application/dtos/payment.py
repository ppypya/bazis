import hashlib
import json
from dataclasses import dataclass
from decimal import Decimal
from typing import Any

from domain.value_objects.money import Currency


@dataclass(frozen=True)
class CreatePaymentCommand:
    amount: Decimal
    currency: Currency
    description: str
    metadata: dict[str, Any]
    webhook_url: str
    idempotency_key: str

    def fingerprint(self) -> str:
        body = {
            "amount": format(self.amount.quantize(Decimal("0.01")), ".2f"),
            "currency": self.currency.value,
            "description": self.description,
            "metadata": self.metadata,
            "webhook_url": self.webhook_url,
        }

        encoded = json.dumps(
            body, sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False
        ).encode()

        return hashlib.sha256(encoded).hexdigest()
