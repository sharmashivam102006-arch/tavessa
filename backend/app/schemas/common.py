"""Shared Pydantic shapes: health, pagination, error envelope."""
from pydantic import BaseModel, Field


class HealthResponse(BaseModel):
    status: str = Field(examples=["ok"])
    database: str = Field(examples=["ok"])
    environment: str = Field(examples=["development"])
    version: str = Field(examples=["0.1.0"])


class Pagination(BaseModel):
    page: int
    per_page: int
    total: int
    total_pages: int
