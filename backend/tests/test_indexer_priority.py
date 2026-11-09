"""Tracker order decides between otherwise equal releases."""

from datetime import datetime, timezone
from types import SimpleNamespace

from app.api.search import search_result_order
from app.services.upgrade_candidates import _candidate_sort_key


def _candidate(name: str, *, priority: int, seeders: int | None, score: int = 145, quality: int = 0, revision: int = 0):
    return SimpleNamespace(
        name=name, quality_preference=quality, custom_format_score=score, revision_rank=revision, indexer_priority=priority,
        result=SimpleNamespace(seeders=seeders, published=None, title=name, guid=name, download_url=None),
    )


def _ranked(*candidates) -> list[str]:
    return [item.name for item in sorted(candidates, key=_candidate_sort_key)]


def test_the_first_tracker_wins_despite_fewer_seeders() -> None:
    assert _ranked(_candidate("TL", priority=6, seeders=61), _candidate("Aither", priority=1, seeders=33)) == ["Aither", "TL"]


def test_seeders_decide_between_trackers_at_the_same_position() -> None:
    assert _ranked(_candidate("thin", priority=1, seeders=2), _candidate("busy", priority=1, seeders=50)) == ["busy", "thin"]


def test_a_copy_nobody_seeds_loses_to_a_lower_tracker() -> None:
    assert _ranked(_candidate("dead", priority=1, seeders=0), _candidate("alive", priority=6, seeders=1)) == ["alive", "dead"]
    assert _ranked(_candidate("unknown", priority=1, seeders=None), _candidate("alive", priority=6, seeders=1)) == ["alive", "unknown"]


def test_tracker_order_never_beats_quality_or_score() -> None:
    assert _ranked(_candidate("first", priority=1, seeders=9, quality=2), _candidate("better", priority=6, seeders=9, quality=1)) == ["better", "first"]
    assert _ranked(_candidate("first", priority=1, seeders=9, score=120), _candidate("better", priority=6, seeders=9, score=145)) == ["better", "first"]


def _search_row(name: str, *, priority: int, seeders: int):
    return SimpleNamespace(name=name, seeders=seeders, custom_format_score=145, created_at=datetime(2026, 1, 1, tzinfo=timezone.utc),
                           custom_format_snapshot={"quality_preference": 5, "indexer_priority": priority})


def test_interactive_search_follows_the_same_order() -> None:
    rows = [_search_row("TL", priority=6, seeders=61), _search_row("Aither", priority=1, seeders=33), _search_row("Dead", priority=1, seeders=0)]
    assert [row.name for row in sorted(rows, key=search_result_order)] == ["Aither", "TL", "Dead"]
