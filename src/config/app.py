from pydantic import BaseModel, Field, SecretStr


class AppSettings(BaseModel):
    api_key: SecretStr = Field(min_length=16)
    log_level: str = "INFO"
    processing_lease_seconds: float = Field(default=90, ge=10)
    operation_timeout_seconds: float = Field(default=20, gt=0)
