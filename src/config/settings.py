from typing import Self

from pydantic import model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

from config.app import AppSettings
from config.broker import BrokerSettings
from config.cors import CorsSettings
from config.database import DatabaseSettings
from config.external_apis import ExternalAPISettings


class Settings(
    AppSettings, DatabaseSettings, BrokerSettings, ExternalAPISettings, CorsSettings, BaseSettings
):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    @model_validator(mode="after")
    def validate_timeouts(self) -> Self:
        if self.gateway_max_delay_seconds < self.gateway_min_delay_seconds:
            raise ValueError("Gateway max delay must be >= min delay")
        if self.gateway_max_delay_seconds >= self.operation_timeout_seconds:
            raise ValueError("Operation timeout must exceed gateway max delay")
        if self.processing_lease_seconds <= 2 * self.operation_timeout_seconds + 10:
            raise ValueError("Processing lease must exceed two operation timeouts plus 10s")

        return self
