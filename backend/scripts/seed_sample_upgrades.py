"""Insert one sample upgrade candidate per upgrade type, for looking at the page.

Temporary: these are not produced by a search. They reference real seasons so
titles resolve, but their indexer result is invented and the download URL is
deliberately empty, so executing one would fail. Remove them with
`python scripts/seed_sample_upgrades.py --clear`.
"""

from __future__ import annotations

import argparse
import json
import re
import sqlite3
import uuid
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
DB = REPO / ".local" / "medialogue.db"

#: A marker in comparison_snapshot, so the samples can be found and deleted
#: again without touching candidates a real search produced.
MARKER = "sample_upgrade"

#: Media folder paths in this library are UNC, so the sample folder joins the
#: configured root with a backslash rather than os.sep.
SEP = "\\"

#: One row per member of UpgradeCandidateResponse.upgrade_types, so every badge
#: the page can draw is visible at once.
SAMPLES: list[dict] = [
    {
        "types": ["quality"],
        "current_release_name": "Show.S01.1080p.WEB-DL.DD5.1.H.264-GROUP",
        "current_quality": "1080p WEB-DL",
        "title": "Show.S01.2160p.UHD.BluRay.REMUX.DV.HDR.HEVC.TrueHD.7.1-GROUP",
        "quality": "2160p BluRay REMUX",
        "size": 92 * 1024**3,
        "seeders": 34,
        "leechers": 2,
    },
    {
        "types": ["custom_format"],
        "current_release_name": "Show.S02.2160p.BluRay.REMUX.HEVC.DTS-HD.MA.5.1-GROUP",
        "current_quality": "2160p BluRay REMUX",
        "title": "Show.S02.2160p.BluRay.REMUX.DV.HDR10+.HEVC.TrueHD.Atmos.7.1-OTHER",
        "quality": "2160p BluRay REMUX",
        "size": 88 * 1024**3,
        "seeders": 21,
        "leechers": 0,
    },
    {
        "types": ["season_pack"],
        "current_release_name": "Show.S03E01.1080p.BluRay.x264-GROUP",
        "replaces": [f"Show.S03E{number:02d}.1080p.BluRay.x264-GROUP" for number in range(1, 11)],
        "current_quality": "1080p Encode",
        "title": "Show.S03.COMPLETE.1080p.BluRay.REMUX.AVC.DTS-HD.MA.5.1-PACK",
        "quality": "1080p BluRay REMUX",
        "size": 61 * 1024**3,
        "seeders": 12,
        "leechers": 1,
    },
    {
        "types": ["repack"],
        "current_release_name": "Show.S04.1080p.BluRay.REMUX.AVC.DTS-HD.MA.5.1-GROUP",
        "current_quality": "1080p BluRay REMUX",
        "title": "Show.S04.1080p.BluRay.REMUX.AVC.DTS-HD.MA.5.1.REPACK-GROUP",
        "quality": "1080p BluRay REMUX",
        "size": 58 * 1024**3,
        "seeders": 9,
        "leechers": 0,
    },
    {
        "types": ["proper"],
        "current_release_name": "Show.S05.2160p.WEB-DL.DDP5.1.H.265-GROUP",
        "current_quality": "2160p WEB-DL",
        "title": "Show.S05.PROPER.2160p.WEB-DL.DDP5.1.Atmos.H.265-GROUP",
        "quality": "2160p WEB-DL",
        "size": 44 * 1024**3,
        "seeders": 47,
        "leechers": 3,
    },
    {
        "types": ["revision", "custom_format"],
        "current_release_name": "Show.S06.2160p.BluRay.REMUX.HEVC.DTS-HD.MA.5.1-GROUP",
        "current_quality": "2160p BluRay REMUX",
        "title": "Show.S06.RERIP.2160p.BluRay.REMUX.HEVC.TrueHD.7.1-GROUP",
        "quality": "2160p BluRay REMUX",
        "size": 90 * 1024**3,
        "seeders": 6,
        "leechers": 4,
        # One blocked row, so the refusal path is visible too.
    },
]


#: A movie sample needs a movie to point at. The library may legitimately have
#: none, and current_paths is derived from a real MediaDirectory, so the sample
#: brings its own title, release and folder. All three carry the marker in a way
#: --clear can find, and the folder is never touched on disk.
#: One movie per upgrade type, so the movie row can be compared with the show
#: row for every reason a candidate exists. Editions are included because a
#: movie folder commonly carries one and a show folder never does.
MOVIE_SAMPLES: list[dict] = [
    {
        "title": "Blade Runner 2049", "year": 2017, "tmdb_id": 335984, "edition": None,
        "types": ["quality"],
        "folder": "Blade.Runner.2049.2017.1080p.BluRay.x264-GROUP",
        "current_quality": "1080p Encode",
        "candidate": "Blade.Runner.2049.2017.2160p.UHD.BluRay.REMUX.DV.HDR.HEVC.TrueHD.7.1.Atmos-OTHER",
        "quality": "2160p BluRay REMUX",
        "size": 78 * 1024**3, "seeders": 58, "leechers": 4,
    },
    {
        "title": "Dune", "year": 2021, "tmdb_id": 438631, "edition": None,
        "types": ["custom_format"],
        "folder": "Dune.2021.2160p.UHD.BluRay.REMUX.HDR.HEVC.DTS-HD.MA.5.1-GROUP",
        "current_quality": "2160p BluRay REMUX",
        "candidate": "Dune.2021.2160p.UHD.BluRay.REMUX.DV.HDR10+.HEVC.TrueHD.Atmos.7.1-OTHER",
        "quality": "2160p BluRay REMUX",
        "size": 84 * 1024**3, "seeders": 31, "leechers": 1,
    },
    {
        "title": "Blade Runner", "year": 1982, "tmdb_id": 78, "edition": "The Final Cut",
        "types": ["quality", "custom_format"],
        "folder": "Blade.Runner.1982.The.Final.Cut.1080p.BluRay.x264-GROUP",
        "current_quality": "1080p Encode",
        "candidate": "Blade.Runner.1982.The.Final.Cut.2160p.UHD.BluRay.REMUX.DV.HDR.HEVC.TrueHD.5.1-OTHER",
        "quality": "2160p BluRay REMUX",
        "size": 71 * 1024**3, "seeders": 24, "leechers": 2,
    },
    {
        "title": "Aliens", "year": 1986, "tmdb_id": 679, "edition": "Extended Cut",
        "types": ["repack"],
        "folder": "Aliens.1986.Extended.Cut.2160p.UHD.BluRay.REMUX.HDR.HEVC.DTS-HD.MA.5.1-GROUP",
        "current_quality": "2160p BluRay REMUX",
        "candidate": "Aliens.1986.Extended.Cut.2160p.UHD.BluRay.REMUX.HDR.HEVC.DTS-HD.MA.5.1.REPACK-GROUP",
        "quality": "2160p BluRay REMUX",
        "size": 69 * 1024**3, "seeders": 11, "leechers": 0,
    },
    {
        "title": "The Lord of the Rings: The Two Towers", "year": 2002, "tmdb_id": 121, "edition": "Extended Edition",
        "types": ["proper"],
        "folder": "The.Lord.of.the.Rings.The.Two.Towers.2002.Extended.2160p.WEB-DL.DDP5.1.H.265-GROUP",
        "current_quality": "2160p WEB-DL",
        "candidate": "The.Lord.of.the.Rings.The.Two.Towers.2002.Extended.PROPER.2160p.WEB-DL.DDP5.1.Atmos.H.265-GROUP",
        "quality": "2160p WEB-DL",
        "size": 52 * 1024**3, "seeders": 63, "leechers": 5,
    },
    {
        "title": "Apocalypse Now", "year": 1979, "tmdb_id": 28, "edition": "Redux",
        "types": ["revision", "custom_format"],
        "folder": "Apocalypse.Now.1979.Redux.2160p.UHD.BluRay.REMUX.HDR.HEVC.DTS-HD.MA.5.1-GROUP",
        "current_quality": "2160p BluRay REMUX",
        "candidate": "Apocalypse.Now.1979.Redux.RERIP.2160p.UHD.BluRay.REMUX.HDR.HEVC.TrueHD.7.1-GROUP",
        "quality": "2160p BluRay REMUX",
        "size": 76 * 1024**3, "seeders": 8, "leechers": 3,
    },
    {
        "title": "The Thing", "year": 1982, "tmdb_id": 1091, "edition": None,
        # season_pack is a show-only reason, so a movie shows the closest
        # equivalent: one folder replacing several loose copies.
        "types": ["quality"],
        "folder": "The.Thing.1982.1080p.WEB-DL.DD5.1.H.264-GROUP",
        "current_quality": "1080p WEB-DL",
        "candidate": "The.Thing.1982.2160p.UHD.BluRay.REMUX.DV.HDR.HEVC.TrueHD.5.1-OTHER",
        "quality": "2160p BluRay REMUX",
        "size": 64 * 1024**3, "seeders": 42, "leechers": 2,
    },
]

#: Fixed id bases so re-seeding reuses the same rows instead of piling up.
MOVIE_ID_BASE = 0x3D1EA000


#: Rough stand-ins for the Custom Formats a library usually defines, keyed by
#: the token that would match them. The real scorer evaluates configured formats
#: under the release's own quality profile; a sample cannot, so it scores both
#: sides with the same table and stays internally consistent.
SAMPLE_FORMATS: list[tuple[str, str, int]] = [
    ("DV", "Dolby Vision", 45),
    ("HDR10+", "HDR10+", 35),
    ("HDR", "HDR10", 25),
    ("REMUX", "Remux", 70),
    ("TrueHD", "TrueHD", 55),
    ("DTS-HD", "DTS-HD MA", 25),
    ("HEVC", "HEVC", 15),
    ("WEB-DL", "WEB-DL", 10),
    ("REPACK", "Repack", 5),
    ("PROPER", "Proper", 15),
    ("RERIP", "Rerip", 5),
    ("x264", "h264", 0),
    ("H", "h264", 0),
    ("DDP5", "Dolby Digital Plus", -5),
    ("DD5", "Dolby Digital", -10),
]


def _sample_id(name: str) -> str:
    return uuid.uuid5(uuid.NAMESPACE_URL, f"{MARKER}:{name}").hex


def _sample_result(name: str, contribution: int, *, regex_match: str | None = None) -> dict:
    condition = {
        "condition_id": f"{_sample_id(name)}-sample",
        "condition_type": "release_title",
        "matched": True,
        "effective_result": True,
        "required": False,
        "negated": False,
        "regex_match": regex_match,
    }
    return {
        "custom_format_id": _sample_id(name),
        "custom_format_name": name,
        "matched": True,
        "raw_matched": True,
        "suppressed_by": [],
        "suppressed_by_names": [],
        "configured_score": contribution,
        "contribution": contribution,
        "conditions": [condition],
    }


def _take_over(matched: list[dict], *, name: str, contribution: int, supersedes: set[str]) -> None:
    takeover_id = _sample_id(name)
    for item in matched:
        if item["custom_format_name"] not in supersedes or not item["matched"]:
            continue
        item["matched"] = False
        item["contribution"] = 0
        item["suppressed_by"] = [takeover_id]
        item["suppressed_by_names"] = [name]
    matched.append(_sample_result(name, contribution))


def _matched_formats(release_name: str) -> list[dict]:
    """Score a release name with the sample table, as the real scorer would.

    Both sides go through this, so a candidate's total, its current total and
    the difference between them always agree with the listed formats.
    """

    tokens = {token.casefold() for token in re.split(r"[.-]", release_name) if token}
    matched: list[dict] = []
    for token, name, contribution in SAMPLE_FORMATS:
        if token.casefold() in tokens and not any(item["custom_format_name"] == name for item in matched):
            matched.append(_sample_result(name, contribution, regex_match=token))

    # Samples must model the same built-in takeover rules as real evaluation.
    # Otherwise the Upgrade Media page can appear broken even though a real
    # indexer result correctly suppresses the broader formats.
    has_dv = re.search(r"(?i)(?<![a-z0-9])(?:dv|dovi|dolby[ .]?vision)(?![a-z0-9])", release_name) is not None
    has_hdr10_plus = re.search(r"(?i)(?<![a-z0-9])hdr10\+(?![a-z0-9])", release_name) is not None
    has_hdr10 = re.search(r"(?i)(?<![a-z0-9])hdr(?:10)?(?!\+)(?![a-z0-9])", release_name) is not None
    if has_dv and has_hdr10_plus:
        _take_over(
            matched,
            name="DV HDR10+",
            contribution=60,
            supersedes={"Dolby Vision", "HDR10+", "HDR10"},
        )
    elif has_dv and has_hdr10:
        _take_over(
            matched,
            name="DV HDR10",
            contribution=50,
            supersedes={"Dolby Vision", "HDR10"},
        )

    has_truehd = "truehd" in tokens
    has_atmos = "atmos" in tokens
    if has_truehd and has_atmos:
        _take_over(matched, name="TrueHD Atmos", contribution=60, supersedes={"TrueHD"})
    return matched


def _comparison(
    current_name: str,
    candidate_name: str,
    replaces: list[str] | None = None,
    current_quality: str | None = None,
) -> tuple[dict, list[dict], int, int]:
    """Snapshot, gained formats and both totals for one sample pair.

    ``replaces`` names every release the candidate would take the place of, as a
    season pack does with a season of episode torrents.
    """

    current = _matched_formats(current_name)
    candidate = _matched_formats(candidate_name)
    previous = {
        item["custom_format_name"]: item["contribution"]
        for item in current
        if item["matched"]
    }
    added = [
        {
            "custom_format_id": item["custom_format_id"],
            "custom_format_name": item["custom_format_name"],
            "contribution": item["contribution"],
            "previous_contribution": previous.get(item["custom_format_name"]),
            "delta": item["contribution"] - previous.get(item["custom_format_name"], 0),
            "new": item["custom_format_name"] not in previous,
        }
        for item in candidate
        if item["matched"]
        if item["contribution"] > previous.get(item["custom_format_name"], 0)
    ]
    snapshot = {
        MARKER: True,
        "note": "Seeded for UI review; not a real search result.",
        "current": {
            "score_snapshot": {"formats": current},
            "releases": [
                {
                    "release_id": uuid.uuid5(uuid.NAMESPACE_URL, f"{MARKER}:{name}").hex,
                    "release_name": name,
                    "quality": current_quality,
                    "custom_format_score": sum(item["contribution"] for item in _matched_formats(name)),
                }
                for name in (replaces or [current_name])
            ],
        },
        "candidate": {"formats": candidate},
    }
    return (
        snapshot,
        added,
        sum(item["contribution"] for item in current),
        sum(item["contribution"] for item in candidate),
    )


def _ids(index: int) -> tuple[str, str, str]:
    """Stable ids per sample, so re-seeding replaces rather than duplicates."""

    base = MOVIE_ID_BASE + index * 16
    return uuid.UUID(int=base).hex, uuid.UUID(int=base + 1).hex, uuid.UUID(int=base + 2).hex


def _seed_movies(connection: sqlite3.Connection) -> list[tuple[dict, str, str]]:
    """Insert each sample movie with a release and folder. Returns their ids.

    The library may legitimately hold no movies, and both the title and the path
    on an upgrade row are derived from real rows, so a movie sample has to bring
    its own. Nothing is written to disk; the folder is a record only.
    """

    root = connection.execute(
        "select id, resolved_root_path from storage_roots where media_type = 'MOVIES' limit 1"
    ).fetchone()
    if root is None:
        print("No movies media folder configured; skipping the movie samples.")
        return []
    root_id, root_path = root

    seeded: list[tuple[dict, str, str]] = []
    for index, sample in enumerate(MOVIE_SAMPLES):
        movie_id, release_id, directory_id = _ids(index)
        path = f"{root_path}{SEP}{sample['folder']}"
        connection.execute(
            """insert or replace into movies
               (id, title, sort_title, year, tmdb_id, overview, monitored, identity_state,
                manual_identity_override, revision, created_at, updated_at)
               values (?, ?, ?, ?, ?, ?, 1, 'MATCHED', 0, 1, datetime('now'), datetime('now'))""",
            (movie_id, sample["title"], sample["title"], sample["year"], sample["tmdb_id"],
             f"Seeded for UI review ({MARKER})."),
        )
        quality = connection.execute(
            "select id from quality_definitions where name = ? limit 1", (sample["current_quality"],)
        ).fetchone()
        current_score = sum(item["contribution"] for item in _matched_formats(sample["folder"]))
        connection.execute(
            """insert or replace into movie_releases
               (id, movie_id, raw_release_name, parsed_title, parsed_year, parsed_edition,
                effective_edition, quality_definition_id, release_group, release_state, first_seen_at,
                original_custom_format_score, current_custom_format_score, parse_snapshot,
                edition_override_active, audio_languages, audio_language_override_active, version_kind,
                created_at, updated_at)
               values (?, ?, ?, ?, ?, ?, ?, ?, 'GROUP', 'CURRENT', datetime('now'),
                       ?, ?, ?, 0, '[]', 0, 'original', datetime('now'), datetime('now'))""",
            (release_id, movie_id, sample["folder"], sample["title"], sample["year"],
             sample["edition"], sample["edition"], quality[0] if quality else None,
             current_score, current_score, json.dumps({MARKER: True})),
        )
        connection.execute(
            """insert or replace into media_directories
               (id, storage_root_id, reported_path, resolved_path, movie_release_id, "exists",
                first_seen_at, last_seen_at, missing_check_count, source_type)
               values (?, ?, ?, ?, ?, 1, datetime('now'), datetime('now'), 0, 'FILESYSTEM')""",
            (directory_id, root_id, path, path, release_id),
        )
        seeded.append((sample, movie_id, release_id))
    return seeded


def _targets(connection: sqlite3.Connection) -> list[tuple[str, str, int, str]]:
    """Real seasons, each with a real show release to point current_release_ids at.

    current_paths is not stored on the candidate: the API derives it by joining
    the release through its mapped media files to their directories. A sample
    with no release id therefore renders "Path not recorded", so each one has to
    borrow a release that genuinely has files on disk.
    """

    rows = connection.execute(
        """select seasons.id, shows.title, seasons.season_number, min(show_releases.id)
           from seasons
           join shows on shows.id = seasons.show_id
           join episodes on episodes.season_id = seasons.id
           join episode_media_maps on episode_media_maps.episode_id = episodes.id
           join media_files on media_files.id = episode_media_maps.media_file_id
           join show_releases on show_releases.id = episode_media_maps.show_release_id
           where seasons.season_number > 0 and media_files."exists" = 1
           group by seasons.id
           order by shows.title, seasons.season_number"""
    ).fetchall()
    if not rows:
        raise SystemExit("No mapped show media in the library; scan a show folder first.")
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
        season_id, show_title, season_number, release_id = targets[index % len(targets)]
        current_name = sample["current_release_name"].replace("Show", show_title.replace(" ", "."))
        candidate_name = sample["title"].replace("Show", show_title.replace(" ", "."))
        replaces = [
            value.replace("Show", show_title.replace(" ", ".")) for value in sample.get("replaces", [])
        ]
        snapshot, added, current_score, score = _comparison(
            current_name, candidate_name, replaces, sample["current_quality"]
        )
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
                       ?, ?, ?, ?,
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
                json.dumps([release_id]),
                current_name,
                sample["current_quality"],
                current_score,
                indexer_id,
                indexer_name,
                f"sample-upgrade-{index}",
                candidate_name,
                sample["quality"],
                score,
                score - current_score,
                sample["size"],
                sample["seeders"],
                sample["leechers"],
                json.dumps(sample["types"]),
                json.dumps(added),
                1,
                None,
                json.dumps(snapshot),
            ),
        )
        inserted += 1
        del season_number
    for sample, movie_id, movie_release_id in _seed_movies(connection):
        snapshot, added, current_score, score = _comparison(
            sample["folder"], sample["candidate"], None, sample["current_quality"]
        )
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
               values (?, ?, 'MOVIES', ?, NULL,
                       ?, ?, ?, ?,
                       ?, ?, ?, NULL,
                       ?, ?, ?, ?,
                       ?, ?, ?, datetime('now', '-1 day'),
                       ?, ?, ?, ?,
                       ?, '[]',
                       datetime('now'), datetime('now'), datetime('now'))""",
            (
                uuid.uuid4().hex,
                f"sample:movie:{movie_id}",
                movie_id,
                json.dumps([movie_release_id]),
                sample["folder"],
                sample["current_quality"],
                current_score,
                indexer_id,
                indexer_name,
                f"sample-upgrade-movie-{movie_id}",
                sample["candidate"],
                sample["quality"],
                score,
                score - current_score,
                sample["size"],
                sample["seeders"],
                sample["leechers"],
                json.dumps(sample["types"]),
                json.dumps(added),
                1,
                None,
                json.dumps(snapshot),
            ),
        )
        inserted += 1

    connection.commit()
    print(f"inserted {inserted} sample upgrade candidates")


def clear() -> None:
    connection = sqlite3.connect(DB)
    cursor = connection.execute(
        "delete from upgrade_candidates where comparison_snapshot like ?", (f'%"{MARKER}"%',)
    )
    # The sample movie exists only to give the movie candidate a real title and
    # folder, so it leaves with it. Its folder on disk is never touched.
    for index in range(len(MOVIE_SAMPLES)):
        movie_id, release_id, directory_id = _ids(index)
        connection.execute("delete from media_directories where id = ?", (directory_id,))
        connection.execute("delete from movie_releases where id = ?", (release_id,))
        connection.execute("delete from movies where id = ?", (movie_id,))
    connection.commit()
    print(f"removed {cursor.rowcount} sample upgrade candidates")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--clear", action="store_true", help="remove the samples again")
    args = parser.parse_args()
    clear() if args.clear else seed()
