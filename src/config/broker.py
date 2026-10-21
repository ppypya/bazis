from pydantic import BaseModel, Field


class BrokerSettings(BaseModel):
    rabbitmq_url: str = "amqp://payments:payments@localhost:5673/"
    retry_delay_seconds: float = Field(default=5, ge=0.1)
    outbox_poll_seconds: float = Field(default=0.5, ge=0.01)
