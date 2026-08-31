"""Insert one sample upgrade candidate per upgrade type, for looking at the page.

Temporary: these are not produced by a search. They reference real seasons so
titles resolve, but their indexer result is invented and the download URL is
deliberately empty, so executing one would fail. Remove them with
`python scripts/seed_sample_upgrades.py --clear`.
"""

from __future__ import annotations

import argparse
import json
import sqlite3
import uuid
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
DB = REPO / ".local" / "medialogue.db"

#: A marker in comparison_snapshot, so the samples can be found and deleted
#: again without touching candidates a real search produced.
MARKER = "sample_upgrade"

#: One row per member of UpgradeCandidateResponse.upgrade_types, so every badge
#: the page can draw is visible at once.
SAMPLES: list[dict] = [
    {
        "types": ["quality"],
        "current_release_name": "Show.S01.1080p.WEB-DL.DD5.1.H.264-GROUP",
        "current_quality": "1080p WEB-DL",
        "current_score": 40,
        "title": "Show.S01.2160p.UHD.BluRay.REMUX.DV.HDR.HEVC.TrueHD.7.1-GROUP",
        "quality": "2160p BluRay REMUX",
        "score": 215,
        "size": 92 * 1024**3,
        "seeders": 34,
        "leechers": 2,
        "added": [{"name": "Dolby Vision", "delta": 45}, {"name": "TrueHD Atmos", "delta": 60}],
    },
    {
        "types": ["custom_format"],
        "current_release_name": "Show.S02.2160p.BluRay.REMUX.HEVC.DTS-HD.MA.5.1-GROUP",
        "current_quality": "2160p BluRay REMUX",
        "current_score": 95,
        "title": "Show.S02.2160p.BluRay.REMUX.DV.HDR10+.HEVC.TrueHD.Atmos.7.1-OTHER",
        "quality": "2160p BluRay REMUX",
        "score": 215,
        "size": 88 * 1024**3,
        "seeders": 21,
        "leechers": 0,
        "added": [{"name": "DV HDR10+", "delta": 60}, {"name": "TrueHD Atmos", "delta": 60}],
    },
    {
        "types": ["season_pack"],
        "current_release_name": "Show.S03E01-E10.1080p.BluRay.x264-GROUP (10 files)",
        "current_quality": "1080p BluRay",
        "current_score": 50,
        "title": "Show.S03.COMPLETE.1080p.BluRay.REMUX.AVC.DTS-HD.MA.5.1-PACK",
        "quality": "1080p BluRay REMUX",
        "score": 120,
        "size": 61 * 1024**3,
        "seeders": 12,
        "leechers": 1,
        "added": [{"name": "Remux", "delta": 70}],
    },
    {
        "types": ["repack"],
        "current_release_name": "Show.S04.1080p.BluRay.REMUX.AVC.DTS-HD.MA.5.1-GROUP",
        "current_quality": "1080p BluRay REMUX",
        "current_score": 120,
        "title": "Show.S04.1080p.BluRay.REMUX.AVC.DTS-HD.MA.5.1.REPACK-GROUP",
        "quality": "1080p BluRay REMUX",
        "score": 125,
        "size": 58 * 1024**3,
        "seeders": 9,
        "leechers": 0,
        "added": [{"name": "Repack", "delta": 5}],
    },
    {
        "types": ["proper"],
        "current_release_name": "Show.S05.2160p.WEB-DL.DDP5.1.H.265-GROUP",
        "current_quality": "2160p WEB-DL",
        "current_score": 80,
        "title": "Show.S05.PROPER.2160p.WEB-DL.DDP5.1.Atmos.H.265-GROUP",
        "quality": "2160p WEB-DL",
        "score": 95,
        "size": 44 * 1024**3,
        "seeders": 47,
        "leechers": 3,
        "added": [{"name": "Proper", "delta": 15}],
    },
    {
        "types": ["revision", "custom_format"],
        "current_release_name": "Show.S06.2160p.BluRay.REMUX.HEVC.DTS-HD.MA.5.1-GROUP",
        "current_quality": "2160p BluRay REMUX",
        "current_score": 95,
        "title": "Show.S06.2160p.BluRay.REMUX.HEVC.TrueHD.7.1.v2-GROUP",
        "quality": "2160p BluRay REMUX",
        "score": 155,
        "size": 90 * 1024**3,
        "seeders": 6,
        "leechers": 4,
        "added": [{"name": "TrueHD", "delta": 55}, {"name": "Revision", "delta": 5}],
        # One blocked row, so the refusal path is visible too.
        "eligible": False,
        "block_reason": "No download client is configured for this indexer.",
    },
]


def _targets(connection: sqlite3.Connection) -> list[tuple[str, str, int]]:
    """Real seasons to hang the samples on, so media titles resolve."""

    rows = connection.execute(
        """select seasons.id, shows.title, seasons.season_number
           from seasons join shows on shows.id = seasons.show_id
           where seasons.season_number > 0
           order by shows.title, seasons.season_number"""
    ).fetchall()
    if not rows:
        raise SystemExit("No seasons in the library; scan a show folder first.")
    return rows


def seed() -> None:
    connection = sqlite3.connect(DB)
    targets = _targets(connection)
    # The indexers table holds runtime health only; names live in the config
    # store. The id keeps the foreign key valid, the label is just display text.
    row = connection.execute("select id from indexers limit 1").fetchone()
    indexer_id = row[0] if row else None
    indexer_name = "Sample indexer"

    inserted = 0
    for index, sample in enumerate(SAMPLES):
        season_id, show_title, season_number = targets[index % len(targets)]
        snapshot = {MARKER: True, "note": "Seeded for UI review; not a real search result."}
        connection.execute(
            """insert into upgrade_candidates
               (id, target_key, media_type, movie_id, season_id,
                current_release_ids, current_release_name, current_quality, current_custom_format_score,
                indexer_id, indexer_name, guid, download_url,
                title, quality, custom_format_score, score_delta,
                size, seeders, leechers, published_at,
                upgrade_types, added_custom_formats, execution_eligible, execution_block_reason,
                comparison_snapshot, incoming_release_ids,
                last_searched_at, created_at, updated_at)
               values (?, ?, 'SHOWS', NULL, ?,
                       '[]', ?, ?, ?,
                       ?, ?, ?, NULL,
                       ?, ?, ?, ?,
                       ?, ?, ?, datetime('now', '-2 days'),
                       ?, ?, ?, ?,
                       ?, '[]',
                       datetime('now'), datetime('now'), datetime('now'))""",
            (
                uuid.uuid4().hex,
                f"sample:{index}:{season_id}",
                season_id,
                sample["current_release_name"].replace("Show", show_title.replace(" ", ".")),
                sample["current_quality"],
                sample["current_score"],
                indexer_id,
                indexer_name,
                f"sample-upgrade-{index}",
                sample["title"].replace("Show", show_title.replace(" ", ".")),
                sample["quality"],
                sample["score"],
                sample["score"] - sample["current_score"],
                sample["size"],
                sample["seeders"],
                sample["leechers"],
                json.dumps(sample["types"]),
                json.dumps(sample["added"]),
                1 if sample.get("eligible", True) else 0,
                sample.get("block_reason"),
                json.dumps(snapshot),
            ),
        )
        inserted += 1
        del season_number
    connection.commit()
    print(f"inserted {inserted} sample upgrade candidates")


def clear() -> None:
    connection = sqlite3.connect(DB)
    cursor = connection.execute(
        "delete from upgrade_candidates where comparison_snapshot like ?", (f'%"{MARKER}"%',)
    )
    connection.commit()
    print(f"removed {cursor.rowcount} sample upgrade candidates")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--clear", action="store_true", help="remove the samples again")
    args = parser.parse_args()
    clear() if args.clear else seed()
