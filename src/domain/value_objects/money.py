from dataclasses import dataclass
from decimal import Decimal
from enum import StrEnum

from domain.exceptions import DomainError


class Currency(StrEnum):
    RUB = "RUB"
    USD = "USD"
    EUR = "EUR"


@dataclass(frozen=True)
class Money:
    amount: Decimal
    currency: Currency

    def __post_init__(self) -> None:
        if not self.amount.is_finite() or not 0 < self.amount < Decimal("10000000000000000"):
            raise DomainError("Amount must be positive and fit NUMERIC(18, 2)")
        if self.amount != self.amount.quantize(Decimal("0.01")):
            raise DomainError("Amount must have at most two decimal places")

        object.__setattr__(self, "amount", self.amount.quantize(Decimal("0.01")))
