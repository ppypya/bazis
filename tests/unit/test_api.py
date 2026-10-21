from fastapi.testclient import TestClient

from application.use_cases.create_payment import CreatePayment
from application.use_cases.get_payment import GetPayment
from config.api import create_app
from config.settings import Settings
from infrastructures.webhooks.policy import WebhookURLPolicy
from presentation.api.dependencies import provide_create_payment, provide_get_payment

KEY = "unit-test-api-key-12345"
BODY = {"amount": "123.45", "currency": "RUB", "webhook_url": "https://example.com/hook"}


def make_client(store):
    app = create_app(Settings(api_key=KEY, _env_file=None))
    app.dependency_overrides[provide_create_payment] = lambda: CreatePayment(
        store.uow, WebhookURLPolicy()
    )
    app.dependency_overrides[provide_get_payment] = lambda: GetPayment(store.uow)
    return TestClient(app)


def test_auth_validation_creation_get_and_conflict(store):
    with make_client(store) as client:
        assert client.post("/api/v1/payments", json=BODY).status_code == 401
        assert (
            client.get("/api/v1/payments/00000000-0000-0000-0000-000000000000").status_code == 401
        )

        headers = {"X-API-Key": KEY}
        assert client.post("/api/v1/payments", json=BODY, headers=headers).status_code == 422

        headers["Idempotency-Key"] = "order-42"
        first = client.post("/api/v1/payments", json=BODY, headers=headers)
        assert first.status_code == 202

        second = client.post("/api/v1/payments", json=BODY, headers=headers)
        assert first.json() == second.json()

        result = client.get(first.headers["Location"], headers=headers)
        assert result.status_code == 200
        assert result.json()["amount"] == "123.45"
        assert result.json()["status"] == "pending"

        assert (
            client.post(
                "/api/v1/payments", json={**BODY, "amount": "200"}, headers=headers
            ).status_code
            == 409
        )

        for patch in (
            {"amount": "-1"},
            {"amount": "1.001"},
            {"currency": "BTC"},
            {"webhook_url": "file:///tmp/test"},
            {"webhook_url": "http://user:pass@example.com"},
            {"webhook_url": "http://example.com/hook"},
            {"webhook_url": "https://example.com:8443/hook"},
            {"webhook_url": "https://example.com/hook#fragment"},
            {"webhook_url": "https://127.0.0.1/hook"},
            {"webhook_url": "https://[::1]/hook"},
            {"webhook_url": "https://169.254.169.254/hook"},
        ):
            assert (
                client.post("/api/v1/payments", json={**BODY, **patch}, headers=headers).status_code
                == 422
            )

        assert client.get("/api/v1/payments/not-a-uuid", headers=headers).status_code == 422
        assert (
            client.get(
                "/api/v1/payments/00000000-0000-0000-0000-000000000000", headers=headers
            ).status_code
            == 404
        )

        assert client.get("/docs").status_code == 200
