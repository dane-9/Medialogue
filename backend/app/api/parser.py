from pydantic import BaseModel, Field
from fastapi import APIRouter, Depends

from app.api.dependencies import require_admin
from app.models.auth import AdminUser
from app.parser import list_quality_definitions, parse_release_name

router = APIRouter(tags=["parser"])


class ParserTestRequest(BaseModel):
    name: str = Field(min_length=1, max_length=4096)


@router.post("/parser/test")
async def test_parser(
    payload: ParserTestRequest, _: AdminUser = Depends(require_admin)
) -> dict[str, object]:
    """Return the complete non-mutating, versioned release parse."""
    return parse_release_name(payload.name).to_dict()


@router.get("/parser/quality-definitions", include_in_schema=False)
async def quality_definitions(_: AdminUser = Depends(require_admin)) -> dict[str, object]:
    definitions = list_quality_definitions()
    return {
        "items": [
            {
                "key": item.name,
                "name": item.name,
                "resolution": item.resolution,
                "source": item.source,
                "modifier": item.modifier,
                "rank": item.rank,
            }
            for item in definitions
        ],
        "total": len(definitions),
    }
