from pydantic import BaseModel, Field


class ExternalAPISettings(BaseModel):
    gateway_min_delay_seconds: float = Field(default=2, ge=0)
    gateway_max_delay_seconds: float = Field(default=5, ge=0)
    webhook_timeout_seconds: float = Field(default=10, gt=0)
    webhook_allow_local: bool = False
