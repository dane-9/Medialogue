"""Saved filters for the Movies and Shows lists."""

from tests.test_libraries import client, login  # noqa: F401  (client is a fixture)

MONITORED_4K = {
    "media_type": "movies",
    "label": "Monitored 4K",
    "filters": [
        {"key": "monitored", "type": "equal", "value": [True]},
        {"key": "quality", "type": "equal", "value": ["2160p", "2160p Remux"]},
    ],
}


def test_custom_filters_are_saved_per_media_type_and_can_be_edited_and_deleted(client):
    headers = login(client)

    created = client.post("/api/v1/custom-filters", headers=headers, json=MONITORED_4K)
    assert created.status_code == 201, created.text
    saved = created.json()
    assert saved["label"] == "Monitored 4K"
    assert saved["filters"][0] == {"key": "monitored", "type": "equal", "value": [True]}
    client.post("/api/v1/custom-filters", headers=headers, json={
        "media_type": "shows", "label": "Missing", "filters": [{"key": "episodesMissing", "type": "greaterThan", "value": [0]}],
    })

    movies = client.get("/api/v1/custom-filters", params={"media_type": "movies"}, headers=headers).json()
    assert [row["label"] for row in movies] == ["Monitored 4K"]

    renamed = client.put(f"/api/v1/custom-filters/{saved['id']}", headers=headers, json={**MONITORED_4K, "label": "  Monitored UHD "})
    assert renamed.status_code == 200, renamed.text
    assert renamed.json()["label"] == "Monitored UHD"

    assert client.delete(f"/api/v1/custom-filters/{saved['id']}", headers=headers).status_code == 204
    assert client.get("/api/v1/custom-filters", params={"media_type": "movies"}, headers=headers).json() == []
    assert [row["label"] for row in client.get("/api/v1/custom-filters", headers=headers).json()] == ["Missing"]


def test_a_custom_filter_needs_a_unique_label_conditions_and_a_known_predicate(client):
    headers = login(client)
    assert client.post("/api/v1/custom-filters", headers=headers, json=MONITORED_4K).status_code == 201
    duplicate = client.post("/api/v1/custom-filters", headers=headers, json={**MONITORED_4K, "label": "monitored 4k"})
    assert duplicate.status_code == 409
    assert client.post("/api/v1/custom-filters", headers=headers, json={**MONITORED_4K, "label": "Empty", "filters": []}).status_code == 422
    unknown = {**MONITORED_4K, "label": "Odd", "filters": [{"key": "year", "type": "between", "value": [1]}]}
    assert client.post("/api/v1/custom-filters", headers=headers, json=unknown).status_code == 422
