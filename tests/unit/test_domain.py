from datetime import UTC, datetime
from decimal import Decimal

import pytest

from domain.entities.payment import PaymentStatus
from domain.exceptions import DomainError
from domain.value_objects.money import Currency, Money


@pytest.mark.parametrize("amount", ["0", "-1", "1.001", "NaN", "Infinity", "10000000000000000"])
def test_invalid_money(amount):
    with pytest.raises(DomainError):
        Money(Decimal(amount), Currency.RUB)


async def test_final_result_cannot_be_overwritten(payment):
    payment.finish(PaymentStatus.SUCCEEDED, datetime.now(UTC))
    with pytest.raises(DomainError):
        payment.finish(PaymentStatus.FAILED, datetime.now(UTC))


def test_decimal_normalization():
    assert str(Money(Decimal("12.5"), Currency.USD).amount) == "12.50"
