import asyncio
import os

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.ext.asyncio import create_async_engine
from sqlalchemy import select

from app.core.config import Settings
from app.db import session as db_session
from app.db.base import Base
from app.main import create_app
from app.db.bootstrap import ensure_builtin_custom_formats
from app.models.domain import CustomFormat, MediaScope


def _format_named(payload: dict, name: str) -> dict:
    """Pick one evaluated format by name.

    Built-in formats share the evaluation list, so position is meaningless.
    """

    for item in payload.get("formats", []):
        if item.get("custom_format_name") == name or item.get("name") == name:
            return item
    raise AssertionError(f"{name} not in {[i.get('custom_format_name') or i.get('name') for i in payload.get('formats', [])]}")


def _user_formats(payload: dict) -> list[dict]:
    """Only the formats a test created; built-ins are filtered out."""

    return [item for item in payload.get("items", []) if not item.get("builtin")]



@pytest.fixture
def client(tmp_path):
    db_path = str(tmp_path / "medialogue.db")
    database_url = f"sqlite+aiosqlite:///{db_path}"
    settings = Settings(database_url=database_url, bootstrap_admin=True, config_dir=f"{db_path}.config", secret_key="test-secret-key-123456")
    engine = create_async_engine(database_url)

    async def create_schema():
        async with engine.begin() as connection:
            await connection.run_sync(Base.metadata.create_all)
        await engine.dispose()

    asyncio.run(create_schema())
    app = create_app(settings)
    with TestClient(app) as test_client:
        yield test_client
    asyncio.run(db_session.engine.dispose())
    try:
        os.remove(db_path)
    except FileNotFoundError:
        pass


def _login(client: TestClient) -> dict[str, str]:
    response = client.post("/api/v1/auth/login", json={"username": "admin", "password": "adminadmin"})
    assert response.status_code == 200, response.text
    return {"X-CSRF-Token": response.json()["csrf_token"]}


def _create(client: TestClient, headers: dict[str, str], *, name: str = "Hybrid REMUX") -> dict:
    response = client.post(
        "/api/v1/custom-formats",
        headers=headers,
        json={
            "name": name,
            "description": "Prefer Hybrid remux releases",
            "media_scope": "movies",
            "enabled": True,
            "conditions": [
                {"type": "quality_modifier", "value": "REMUX", "required": True, "name": "Must be remux"},
                {"type": "release_attribute", "value": "Hybrid", "name": "Hybrid"},
            ],
        },
    )
    assert response.status_code == 201, response.text
    return response.json()


def test_custom_format_crud_has_no_intrinsic_score_and_uses_revision(client: TestClient) -> None:
    headers = _login(client)
    created = _create(client, headers)
    assert created["name"] == "Hybrid REMUX"
    assert created["media_scope"] == "movies"
    assert created["condition_count"] == 2
    assert created["used_by_profiles"] == 0
    assert created["revision"] == 1
    assert "score" not in created
    assert all("score" not in condition for condition in created["conditions"])
    assert all(condition["id"] for condition in created["conditions"])

    listed = client.get("/api/v1/custom-formats")
    assert listed.status_code == 200, listed.text
    assert len(_user_formats(listed.json())) == 1

    updated = client.patch(
        f"/api/v1/custom-formats/{created['id']}",
        headers=headers,
        json={"name": "Hybrid BluRay REMUX", "expected_revision": created["revision"]},
    )
    assert updated.status_code == 200, updated.text
    assert updated.json()["name"] == "Hybrid BluRay REMUX"
    assert updated.json()["revision"] == 2

    stale = client.patch(
        f"/api/v1/custom-formats/{created['id']}",
        headers=headers,
        json={"enabled": False, "expected_revision": 1},
    )
    assert stale.status_code == 409, stale.text
    assert stale.json()["error"]["code"] == "REVISION_CONFLICT"

    duplicate = client.post(
        "/api/v1/custom-formats",
        headers=headers,
        json={
            "name": "hybrid bluray remux",
            "media_scope": "movies",
            "conditions": [{"type": "release_attribute", "value": "Hybrid"}],
        },
    )
    assert duplicate.status_code == 409, duplicate.text

    deleted = client.delete(f"/api/v1/custom-formats/{created['id']}", headers=headers)
    assert deleted.status_code == 200, deleted.text
    assert deleted.json()["deleted"] is True


def test_custom_format_sections_and_order_are_persistent(client: TestClient) -> None:
    headers = _login(client)
    created = _create(client, headers, name="My Ordered Format")
    initial = client.get("/api/v1/custom-formats/layout")
    assert initial.status_code == 200, initial.text
    sections = initial.json()["sections"]
    assert all(section["id"] != "custom" and section["name"] != "Custom" for section in sections)
    assert any(section["id"] == "dynamic-range" and section["name"] == "Dynamic Range" for section in sections)
    assert all(section["id"] != "hdr" and section["name"] != "HDR" for section in sections)
    all_ids = [format_id for section in sections for format_id in section["format_ids"]]
    assert created["id"] in all_ids
    assert created["id"] in next(section["format_ids"] for section in sections if section["id"] == "release")

    custom_ids = [created["id"]]
    remaining_ids = [format_id for format_id in all_ids if format_id != created["id"]]
    reordered = {
        "sections": [
            {"id": "favorites", "name": "Favorites", "format_ids": custom_ids},
            {"id": "everything-else", "name": "Everything else", "format_ids": remaining_ids},
        ]
    }
    saved = client.put("/api/v1/custom-formats/layout", headers=headers, json=reordered)
    assert saved.status_code == 200, saved.text
    assert saved.json() == reordered
    assert client.get("/api/v1/custom-formats/layout").json() == reordered

    invalid = client.put(
        "/api/v1/custom-formats/layout",
        headers=headers,
        json={"sections": [{"id": "favorites", "name": "Favorites", "format_ids": []}]},
    )
    assert invalid.status_code == 409, invalid.text


def test_withdrawn_builtin_is_deleted_instead_of_left_disabled(client: TestClient) -> None:
    async def exercise() -> None:
        async with db_session.async_session_factory() as db:
            db.add(CustomFormat(
                name="HDR (any)",
                media_scope=MediaScope.BOTH,
                enabled=False,
                builtin=True,
                builtin_key="hdr-any",
                condition_definition={"schema_version": 1, "conditions": []},
            ))
            await db.commit()
            await ensure_builtin_custom_formats(db)
            await db.commit()
            assert await db.scalar(select(CustomFormat).where(CustomFormat.builtin_key == "hdr-any")) is None

    asyncio.run(exercise())


def test_builtin_can_be_disabled_and_reenabled_with_enabled_only_patch(client: TestClient) -> None:
    headers = _login(client)
    formats = client.get("/api/v1/custom-formats?page_size=250").json()["items"]
    builtin = next(item for item in formats if item["builtin"])

    disabled = client.patch(
        f"/api/v1/custom-formats/{builtin['id']}",
        headers=headers,
        json={"enabled": False, "expected_revision": builtin["revision"]},
    )
    assert disabled.status_code == 200, disabled.text
    assert disabled.json()["enabled"] is False

    reenabled = client.patch(
        f"/api/v1/custom-formats/{builtin['id']}",
        headers=headers,
        json={"enabled": True, "expected_revision": disabled.json()["revision"]},
    )
    assert reenabled.status_code == 200, reenabled.text
    assert reenabled.json()["enabled"] is True

def test_custom_format_test_explains_every_condition_and_parser_output(client: TestClient) -> None:
    headers = _login(client)
    response = client.post(
        "/api/v1/custom-formats/test",
        headers=headers,
        json={
            "release_name": "Inception 2010 Hybrid 2160p UHD BluRay REMUX DV HDR HEVC DTS-HD MA 5.1-LM",
            "indexer": "PTP",
            "custom_format": {
                "name": "Reference",
                "media_scope": "movies",
                "conditions": [
                    {"type": "release_title", "pattern": "Hybrid\\s+2160p", "required": True},
                    {"type": "release_group", "pattern": "^FraMeSToR$"},
                    {"type": "release_group", "pattern": "^LM$"},
                    {"type": "hdr_type", "value": "DV", "required": True},
                    {"type": "indexer", "value": "PTP", "required": True},
                    {"type": "release_attribute", "value": "REPACK", "negate": True, "required": True},
                ],
            },
        },
    )
    assert response.status_code == 200, response.text
    payload = response.json()
    assert payload["evaluation"]["matched"] is True
    assert len(payload["evaluation"]["conditions"]) == 6
    assert payload["parsed"]["quality"]["canonical"] == "2160p BluRay REMUX"
    assert payload["parsed"]["edition"] is None
    assert payload["parsed"]["release_group"] == "LM"
    assert payload["parsed"]["attributes"]["hybrid"] is True
    assert "score" not in payload["evaluation"]


def test_invalid_regex_is_rejected_before_persistence(client: TestClient) -> None:
    headers = _login(client)
    response = client.post(
        "/api/v1/custom-formats",
        headers=headers,
        json={
            "name": "Bad regex",
            "media_scope": "both",
            "conditions": [{"type": "release_title", "pattern": "["}],
        },
    )
    assert response.status_code == 422, response.text
    assert response.json()["error"]["code"] == "INVALID_CUSTOM_FORMAT"


def test_test_all_respects_media_scope_and_enabled_state(client: TestClient) -> None:
    headers = _login(client)
    movie = _create(client, headers, name="Movie Hybrid")
    show = client.post(
        "/api/v1/custom-formats",
        headers=headers,
        json={
            "name": "Show only",
            "media_scope": "shows",
            "conditions": [{"type": "release_attribute", "value": "Hybrid"}],
        },
    ).json()
    disabled = client.post(
        "/api/v1/custom-formats",
        headers=headers,
        json={
            "name": "Disabled",
            "media_scope": "both",
            "enabled": False,
            "conditions": [{"type": "release_attribute", "value": "Hybrid"}],
        },
    ).json()

    tested = client.post(
        "/api/v1/custom-formats/test-all",
        headers=headers,
        json={"release_name": "Movie 2026 Hybrid 2160p UHD BluRay REMUX H.265-GROUP", "media_scope": "movies"},
    )
    assert tested.status_code == 200, tested.text
    ids = {item["custom_format_id"] for item in tested.json()["formats"]}
    assert movie["id"] in ids
    assert show["id"] not in ids
    assert disabled["id"] not in ids
    # Built-in formats share the evaluation, so the total is not this test's
    # to assert. What matters is that the movie-scoped format matched.
    assert _format_named(tested.json(), "Movie Hybrid")["matched"] is True


def test_application_owned_export_import_round_trip(client: TestClient) -> None:
    headers = _login(client)
    created = _create(client, headers)
    exported = client.get(f"/api/v1/custom-formats/{created['id']}/export")
    assert exported.status_code == 200, exported.text
    bundle = exported.json()
    assert bundle["application"] == "Medialogue"
    assert bundle["schema_version"] == 1
    assert len(bundle["custom_formats"]) == 1
    assert "id" not in bundle["custom_formats"][0]
    # Formats still do not own a fixed profile score; rules may carry the
    # relative score offset introduced for variants such as REPACK2.
    assert "score" not in bundle["custom_formats"][0]
    assert all("score" not in condition for condition in bundle["custom_formats"][0]["conditions"])
    assert all("score_offset" in condition for condition in bundle["custom_formats"][0]["conditions"])

    client.delete(f"/api/v1/custom-formats/{created['id']}", headers=headers)
    imported = client.post("/api/v1/custom-formats/import", headers=headers, json=bundle)
    assert imported.status_code == 201, imported.text
    assert imported.json()["count"] == 1
    restored = imported.json()["imported"][0]
    assert restored["name"] == created["name"]
    assert restored["id"] != created["id"]
    assert restored["conditions"][0]["type"] == "quality_modifier"


def test_import_requires_explicit_medialogue_schema_marker(client: TestClient) -> None:
    headers = _login(client)
    unrelated = client.post(
        "/api/v1/custom-formats/import",
        headers=headers,
        json={
            "custom_formats": [
                {
                    "name": "Looks compatible but is unmarked",
                    "conditions": [{"type": "release_attribute", "value": "Hybrid"}],
                }
            ]
        },
    )
    assert unrelated.status_code == 422, unrelated.text
