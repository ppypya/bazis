from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, Header, HTTPException, Response

from application.exceptions import IdempotencyConflict, PaymentNotFound, WebhookRejected
from application.use_cases.create_payment import CreatePayment
from application.use_cases.get_payment import GetPayment
from presentation.api.dependencies import (
    provide_create_payment,
    provide_get_payment,
)
from presentation.api.schemas import CreatePaymentRequest, PaymentAccepted, PaymentResponse

router = APIRouter(prefix="/api/v1/payments", tags=["payments"])


@router.post("", response_model=PaymentAccepted, status_code=202)
async def create_payment(
    body: CreatePaymentRequest,
    response: Response,
    idempotency_key: Annotated[
        str,
        Header(alias="Idempotency-Key", min_length=1, max_length=255, pattern=r"^\S+$"),
    ],
    use_case: Annotated[CreatePayment, Depends(provide_create_payment)],
) -> PaymentAccepted:
    try:
        payment = await use_case.execute(body.to_command(idempotency_key))
    except WebhookRejected as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    except IdempotencyConflict as exc:
        raise HTTPException(status_code=409, detail="Idempotency key payload mismatch") from exc

    response.headers["Location"] = f"/api/v1/payments/{payment.id}"
    return PaymentAccepted(
        payment_id=payment.id, status=payment.status, created_at=payment.created_at
    )


@router.get("/{payment_id}", response_model=PaymentResponse)
async def get_payment(
    payment_id: UUID,
    use_case: Annotated[GetPayment, Depends(provide_get_payment)],
) -> PaymentResponse:
    try:
        payment = await use_case.execute(payment_id)
    except PaymentNotFound as exc:
        raise HTTPException(status_code=404, detail="Payment not found") from exc

    return PaymentResponse.from_payment(payment)
