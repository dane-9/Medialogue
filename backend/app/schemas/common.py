from typing import Generic, TypeVar
from uuid import UUID

from pydantic import BaseModel, ConfigDict


class ORMModel(BaseModel):
    model_config = ConfigDict(from_attributes=True)


T = TypeVar("T")


# The Movies and Shows pages filter and sort the whole library in the browser,
# so their lists can be read in one request.
LIBRARY_PAGE_LIMIT = 10_000


class Collection(BaseModel, Generic[T]):
    items: list[T]
    page: int = 1
    page_size: int = 50
    total: int
    pages: int


class DeleteResponse(BaseModel):
    deleted: bool = True
    id: UUID
