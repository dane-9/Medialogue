from typing import Any, Generic, TypeVar
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field


class ORMModel(BaseModel):
    model_config = ConfigDict(from_attributes=True)


class ErrorDetails(BaseModel):
    code: str
    message: str
    details: Any = Field(default_factory=dict)


class ErrorEnvelope(BaseModel):
    error: ErrorDetails


T = TypeVar("T")


class Collection(BaseModel, Generic[T]):
    items: list[T]
    page: int = 1
    page_size: int = 50
    total: int
    pages: int


class DeleteResponse(BaseModel):
    deleted: bool = True
    id: UUID
