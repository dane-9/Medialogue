from pydantic import BaseModel, Field
from fastapi import APIRouter, Depends

from app.api.dependencies import require_admin
from app.models.auth import AdminUser
from app.parser import parse_release

router = APIRouter(tags=["parser"])


class ParserTestRequest(BaseModel):
    name: str = Field(min_length=1, max_length=4096)


@router.post("/parser/test")
async def test_parser(
    payload: ParserTestRequest, _: AdminUser = Depends(require_admin)
) -> dict[str, object]:
    """Return the complete non-mutating, versioned release parse."""
    return parse_release(payload.name).to_dict()

