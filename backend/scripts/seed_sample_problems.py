"""Insert one sample Problem of every kind, for looking at the Problems page.

Temporary: these are not produced by a scan and reference entities that do not
exist, so anything that reads through to real media will show gaps. Remove them
with `python scripts/seed_sample_problems.py --clear`.
"""

from __future__ import annotations

import argparse
import asyncio
import sqlite3
import uuid
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
DB = REPO / ".local" / "medialogue.db"

#: A marker in details, so the samples can be found and deleted again without
#: touching Problems a real scan filed.
MARKER = "sample_problem"

SHOW_PATH = r"\\TRUENAS\Shows 2\Cartoons\Super Friends"
SEP = "\\"
RELEASE_FOLDER = "Super.Friends.S09.1080p.BluRay.Remux.AVC.DTS-HD.MA.2.0-Tooncore"
EPISODE_FILE_NAMES = [
    "Super.Friends.S09E04.1080p.BluRay.Remux.AVC.DTS-HD.MA.2.0-Tooncore.mkv",
    "Super.Friends.S09E03-E04.1080p.BluRay.Remux.AVC.DTS-HD.MA.2.0-Tooncore.mkv",
]
#: Fixed so re-seeding does not invent new ids for the same sample files.
EPISODE_FILE_IDS = [uuid.UUID(int=0xE9150DE1), uuid.UUID(int=0xE9150DE2)]
MOVIE_PATH = r"\\TRUENAS\Movies\Cartoons\Aladdin.1992.NORDiC.ENG.REMUX.2160p"

SAMPLES: list[tuple[str, str, str, str, dict]] = [
    (
        "PLEX_IDENTITY_MISMATCH", "media_directory", "warning",
        "Plex says this is Super Friends (1980); Medialogue says Super Friends (1973).",
        {"path": SHOW_PATH, "plex_title": "Super Friends", "plex_year": 1980, "local_year": 1973},
    ),
    (
        "DUPLICATE_PHYSICAL_RELEASE", "movie", "warning",
        "Two physical copies claim Aladdin (1992).",
        {"path": MOVIE_PATH, "release_names": [
            "Aladdin.1992.NORDiC.ENG.REMUX.2160p", "Aladdin.1992.1080p.BluRay.x264-GROUP",
        ]},
    ),
    (
        "DUPLICATE_EPISODE_RELEASE", "episode", "warning",
        "S09E04 has multiple physical media files.",
        # Two files, shaped like _refresh_episode_duplicate_problem writes them.
        # An empty media_file_ids list left the page showing a single path for a
        # Problem whose entire point is that there is more than one file.
        {
            "episode": "S09E04",
            "media_file_ids": [str(EPISODE_FILE_IDS[0]), str(EPISODE_FILE_IDS[1])],
            "verified_physical_file_count": 2,
            "media_files": [
                {
                    "media_file_id": str(EPISODE_FILE_IDS[index]),
                    "filename": name,
                    "path": SHOW_PATH + SEP + RELEASE_FOLDER + SEP + name,
                    "storage_root_name": "Shows 2",
                    "release_name": name.removesuffix(".mkv"),
                    "catalogue_exists": True,
                    "physical_exists": True,
                    "missing_since": None,
                    "manual_mapping": False,
                }
                for index, name in enumerate(EPISODE_FILE_NAMES)
            ],
        },
    ),
    (
        "TORRENT_PATH_NOT_FOUND", "torrent", "error",
        "qBittorrent reports a path Medialogue cannot resolve locally.",
        {"reported_path": "/downloads/complete/Aladdin.1992", "info_hash": "b" * 40},
    ),
    (
        "ROOT_UNREACHABLE", "storage_root", "error",
        r"\\TRUENAS\Shows 2\Cartoons could not be read during the last scan.",
        {"path": SHOW_PATH, "affected_media_count": 4},
    ),
    (
        "AMBIGUOUS_REPLACEMENT_TARGET", "movie", "warning",
        "More than one missing release could be replaced by this download.",
        {"path": MOVIE_PATH, "candidate_release_ids": []},
    ),
]


def seed() -> None:
    from app.core.problem_types import PROBLEM_PRESENTATIONS
    from app.models.domain import classify_problem_workflow

    sample_reasons = {reason for reason, *_ in SAMPLES}
    if sample_reasons != set(PROBLEM_PRESENTATIONS):
        raise RuntimeError(
            f"Sample Problems do not match the active taxonomy: "
            f"missing={sorted(set(PROBLEM_PRESENTATIONS) - sample_reasons)}, "
            f"extra={sorted(sample_reasons - set(PROBLEM_PRESENTATIONS))}"
        )

    connection = sqlite3.connect(DB)
    import json

    inserted = 0
    for reason, entity_type, severity, message, details in SAMPLES:
        payload = dict(details)
        payload[MARKER] = True
        connection.execute(
            """insert into problems
               (id, reason, status, workflow, severity, entity_type, entity_id, message, details, created_at)
               values (?, ?, 'OPEN', ?, ?, ?, ?, ?, ?, datetime('now'))""",
            (
                uuid.uuid4().hex,
                reason,
                # SAEnum persists the member name, not its value.
                classify_problem_workflow(reason).name,
                severity.upper(),
                entity_type,
                uuid.uuid4().hex,
                message,
                json.dumps(payload),
            ),
        )
        inserted += 1
    connection.commit()
    print(f"inserted {inserted} sample Problems")


def clear() -> None:
    connection = sqlite3.connect(DB)
    cursor = connection.execute(
        "delete from problems where details like ?", (f'%"{MARKER}"%',)
    )
    connection.commit()
    print(f"removed {cursor.rowcount} sample Problems")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--clear", action="store_true", help="remove the samples again")
    args = parser.parse_args()
    asyncio.run(asyncio.sleep(0))  # keeps the import surface identical either way
    clear() if args.clear else seed()
