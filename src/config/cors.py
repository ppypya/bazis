from pydantic import BaseModel


class CorsSettings(BaseModel):
    cors_origins: list[str] = []
