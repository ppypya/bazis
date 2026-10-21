"""Run inside the api container against the complete Docker Compose stack."""

import json
import os
import threading
import time
from collections import defaultdict
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from uuid import uuid4

import httpx

received = defaultdict(list)
mutex = threading.Lock()


class Receiver(BaseHTTPRequestHandler):
    def do_POST(self):
        body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
        with mutex:
            received[self.path].append((time.monotonic(), body))
            attempt = len(received[self.path])

        status = 500 if self.path == "/dead" or (self.path == "/flaky" and attempt < 3) else 204
        self.send_response(status)
        self.end_headers()

    def log_message(self, *args):
        pass


def main():
    if os.getenv("WEBHOOK_ALLOW_LOCAL", "false").lower() != "true":
        raise SystemExit("Smoke test requires WEBHOOK_ALLOW_LOCAL=true for api and consumer")
    server = ThreadingHTTPServer(("0.0.0.0", 9009), Receiver)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()

    try:
        with httpx.Client(
            base_url="http://localhost:8000",
            headers={"X-API-Key": os.environ["API_KEY"]},
            timeout=10,
        ) as client:
            payments = {}
            for mode in ("ok", "flaky", "dead"):
                body = {
                    "amount": "12.34",
                    "currency": "RUB",
                    "description": f"Smoke test {mode}",
                    "metadata": {"test": True},
                    "webhook_url": f"http://api:9009/{mode}",
                }
                headers = {"Idempotency-Key": f"smoke-{uuid4()}"}
                response = client.post("/api/v1/payments", json=body, headers=headers)
                assert response.status_code == 202, response.text

                payment_id = response.json()["payment_id"]
                duplicate = client.post("/api/v1/payments", json=body, headers=headers)
                assert duplicate.json()["payment_id"] == payment_id
                payments[mode] = payment_id

            deadline = time.monotonic() + 90
            while time.monotonic() < deadline:
                states = {
                    mode: client.get(f"/api/v1/payments/{pid}").json()
                    for mode, pid in payments.items()
                }
                if (
                    states["ok"]["webhook_delivered_at"]
                    and states["flaky"]["webhook_delivered_at"]
                    and states["dead"]["dead_lettered_at"]
                ):
                    break
                time.sleep(0.5)
            else:
                raise AssertionError(f"Processing timed out: {states}")

            assert states["ok"]["attempts"] == 1
            assert states["flaky"]["attempts"] == states["dead"]["attempts"] == 3
            with mutex:
                for mode, count in (("ok", 1), ("flaky", 3), ("dead", 3)):
                    callbacks = received["/" + mode]
                    assert len(callbacks) == count
                    assert all(body == callbacks[0][1] for _, body in callbacks)
                    assert callbacks[0][1]["status"] == states[mode]["status"]

                delays = [received["/flaky"][i + 1][0] - received["/flaky"][i][0] for i in range(2)]

            base_delay = float(os.getenv("RETRY_DELAY_SECONDS", "5"))
            assert delays[0] >= base_delay * 0.9
            assert delays[1] >= base_delay * 1.8

            print("PASS: API → outbox → RabbitMQ → consumer → webhook")
            print(
                "PASS: idempotency, stable payload, 3 attempts, exponential delays, DLQ scheduling"
            )
            print(json.dumps({"payments": payments, "retry_intervals": delays}, indent=2))
    finally:
        server.shutdown()
        server.server_close()
        thread.join()


if __name__ == "__main__":
    main()
