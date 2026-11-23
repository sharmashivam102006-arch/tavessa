"""Shared Pydantic shapes: health, pagination, error envelope.

Stage 3 wire format (api-plan.md § 1): **camelCase** on the wire for
guest endpoints, matching the Stage 2 frontend types field-for-field.
``CamelModel`` applies a snake_case -> camelCase alias generator to
every schema that subclasses it; ``populate_by_name=True`` means
snake_case input is accepted too, but responses always serialise
camelCase.
"""
from pydantic import BaseModel, ConfigDict, Field
from pydantic.alias_generators import to_camel


class CamelModel(BaseModel):
    model_config = ConfigDict(
        alias_generator=to_camel,
        populate_by_name=True,
        from_attributes=True,
    )


class HealthResponse(BaseModel):
    status: str = Field(examples=["ok"])
    database: str = Field(examples=["ok"])
    environment: str = Field(examples=["development"])
    version: str = Field(examples=["0.1.0"])


class Pagination(BaseModel):
    """List pagination envelope. Keys stay snake_case here — the Stage 3
    brief specifies ``{page, per_page, total, total_pages}`` verbatim
    for GET /guest/orders, and api-plan.md § 1 uses the same shape."""

    page: int
    per_page: int
    total: int
    total_pages: int
