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


def _app_database() -> Path:
    """The SQLite file the app itself uses (backend/.env and defaults)."""

    import sys

    sys.path.insert(0, str(REPO))
    from app.core.config import get_settings

    url = get_settings().database_url
    return Path(url.split("///", 1)[1]).resolve()


DB = _app_database()

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
        "title": "Blade Runner", "year": 1982, "tmdb_id": 78, "edition": "Final Cut",
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
        "title": "The Lord of the Rings: The Two Towers", "year": 2002, "tmdb_id": 121, "edition": "Extended Cut",
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
    # Already started, so the In progress and Needs attention tabs have rows.
    # "state" is the execution state; "progress" a download share in 0..1.
    {
        "title": "Heat", "year": 1995, "tmdb_id": 949, "edition": None,
        "types": ["quality"],
        "folder": "Heat.1995.1080p.BluRay.x264-GROUP",
        "current_quality": "1080p Encode",
        "candidate": "Heat.1995.2160p.UHD.BluRay.REMUX.HDR.HEVC.DTS-HD.MA.5.1-OTHER",
        "quality": "2160p BluRay REMUX",
        "size": 66 * 1024**3, "seeders": 19, "leechers": 6,
        "state": "submitted",
    },
    {
        "title": "Interstellar", "year": 2014, "tmdb_id": 157336, "edition": None,
        "types": ["quality", "custom_format"],
        "folder": "Interstellar.2014.1080p.WEB-DL.DDP5.1.H.264-GROUP",
        "current_quality": "1080p WEB-DL",
        "candidate": "Interstellar.2014.2160p.UHD.BluRay.REMUX.HDR.HEVC.DTS-HD.MA.5.1-OTHER",
        "quality": "2160p BluRay REMUX",
        "size": 82 * 1024**3, "seeders": 74, "leechers": 12,
        "state": "downloading", "progress": 0.37,
    },
    {
        "title": "Gladiator", "year": 2000, "tmdb_id": 98, "edition": "Extended Cut",
        "types": ["custom_format"],
        "folder": "Gladiator.2000.Extended.Cut.2160p.UHD.BluRay.REMUX.HDR.HEVC.DTS-HD.MA.5.1-GROUP",
        "current_quality": "2160p BluRay REMUX",
        "candidate": "Gladiator.2000.Extended.Cut.2160p.UHD.BluRay.REMUX.DV.HDR.HEVC.TrueHD.Atmos.7.1-OTHER",
        "quality": "2160p BluRay REMUX",
        "size": 79 * 1024**3, "seeders": 3, "leechers": 9,
        "state": "downloading", "progress": 0.82,
    },
    {
        "title": "The Matrix", "year": 1999, "tmdb_id": 603, "edition": None,
        "types": ["repack"],
        "folder": "The.Matrix.1999.2160p.UHD.BluRay.REMUX.HDR.HEVC.TrueHD.7.1-GROUP",
        "current_quality": "2160p BluRay REMUX",
        "candidate": "The.Matrix.1999.2160p.UHD.BluRay.REMUX.HDR.HEVC.TrueHD.7.1.REPACK-GROUP",
        "quality": "2160p BluRay REMUX",
        "size": 61 * 1024**3, "seeders": 27, "leechers": 1,
        "state": "validating", "progress": 1.0,
    },
    {
        "title": "Jaws", "year": 1975, "tmdb_id": 578, "edition": None,
        "types": ["quality"],
        "folder": "Jaws.1975.720p.BluRay.x264-GROUP",
        "current_quality": "720p Encode",
        "candidate": "Jaws.1975.2160p.UHD.BluRay.REMUX.HDR.HEVC.DTS-HD.MA.5.1-OTHER",
        "quality": "2160p BluRay REMUX",
        "size": 58 * 1024**3, "seeders": 0, "leechers": 4,
        "state": "handoff_attention",
        "error": "qBittorrent never confirmed it added the torrent. Check the client, then retry.",
    },
    {
        "title": "Alien", "year": 1979, "tmdb_id": 348, "edition": "Director's Cut",
        "types": ["proper"],
        "folder": "Alien.1979.Directors.Cut.2160p.UHD.BluRay.REMUX.HDR.HEVC.DTS-HD.MA.5.1-GROUP",
        "current_quality": "2160p BluRay REMUX",
        "candidate": "Alien.1979.Directors.Cut.PROPER.2160p.UHD.BluRay.REMUX.HDR.HEVC.DTS-HD.MA.5.1-GROUP",
        "quality": "2160p BluRay REMUX",
        "size": 63 * 1024**3, "seeders": 14, "leechers": 0,
        "state": "cleanup_required", "progress": 1.0,
        "error": "The upgrade is in place, but the old release's torrent is still in qBittorrent.",
    },
    {
        "title": "Se7en", "year": 1995, "tmdb_id": 807, "edition": None,
        "types": ["quality"],
        "folder": "Se7en.1995.1080p.BluRay.DD5.1.x264-GROUP",
        "current_quality": "1080p Encode",
        "candidate": "Se7en.1995.2160p.UHD.BluRay.REMUX.DV.HDR.HEVC.TrueHD.7.1-OTHER",
        "quality": "2160p BluRay REMUX",
        "size": 72 * 1024**3, "seeders": 33, "leechers": 2,
        "state": "cleanup_required", "progress": 1.0,
        "error": "The upgrade is in place, but the old release's torrent is still in qBittorrent.",
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
    ("DTS-HD MA", "DTS-HD MA", 45),
    ("DTS-HD HRA", "DTS-HD HRA", 20),
    ("HEVC", "HEVC", 15),
    ("H.265", "HEVC", 15),
    ("x265", "HEVC", 15),
    ("WEB-DL", "WEB-DL", 10),
    ("REPACK", "Repack", 5),
    ("PROPER", "Proper", 15),
    ("RERIP", "Rerip", 5),
    ("x264", "h264", 0),
    ("H.264", "h264", 0),
    ("DDP5", "DD+", -5),
    ("DD5", "DD", -10),
]


#: The group the real built-in of the same kind carries, so the breakdown
#: pairs formats the way it would for a real search.
SAMPLE_GROUPS = {
    **dict.fromkeys(("Dolby Vision", "HDR10+", "HDR10", "DV HDR10+", "DV HDR10"), "Dynamic Range"),
    **dict.fromkeys(("TrueHD", "TrueHD Atmos", "DD+ Atmos", "DTS-HD MA", "DTS-HD HRA", "DD+", "DD"), "Audio"),
    **dict.fromkeys(("HEVC", "h264"), "Video"),
    **dict.fromkeys(("Repack", "Proper", "Rerip"), "Release"),
}


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
        "custom_format_group": SAMPLE_GROUPS.get(name),
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


def _sample_token_matches(release_name: str, token: str) -> bool:
    """Match sample tokens across the punctuation accepted by the real parser."""

    parts = [re.escape(part) for part in re.split(r"[- ._]+", token) if part]
    if not parts:
        return False
    phrase = r"[- ._]*".join(parts)
    return re.search(rf"(?i)(?<![a-z0-9]){phrase}(?![a-z0-9])", release_name) is not None


def _matched_formats(release_name: str) -> list[dict]:
    """Score a release name with the sample table, as the real scorer would.

    Both sides go through this, so a candidate's total, its current total and
    the difference between them always agree with the listed formats.
    """

    matched: list[dict] = []
    for token, name, contribution in SAMPLE_FORMATS:
        if _sample_token_matches(release_name, token) and not any(item["custom_format_name"] == name for item in matched):
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

    has_truehd = _sample_token_matches(release_name, "TrueHD")
    has_atmos = _sample_token_matches(release_name, "Atmos")
    if has_truehd and has_atmos:
        _take_over(matched, name="TrueHD Atmos", contribution=60, supersedes={"TrueHD"})
    # As the built-in DD+ Atmos does, Atmos over DD+ replaces plain DD+.
    elif has_atmos and _sample_token_matches(release_name, "DDP5"):
        _take_over(matched, name="DD+ Atmos", contribution=25, supersedes={"DD+"})
    return matched


def _comparison(
    current_name: str,
    candidate_name: str,
    replaces: list[str] | None = None,
    current_quality: str | None = None,
    current_edition: str | None = None,
    candidate_edition: str | None = None,
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
            "edition": current_edition,
            "score_snapshot": {"breakdown": current},
            "releases": [
                {
                    "release_id": uuid.uuid5(uuid.NAMESPACE_URL, f"{MARKER}:{name}").hex,
                    "release_name": name,
                    "quality": current_quality,
                    "edition": current_edition,
                    "custom_format_score": sum(item["contribution"] for item in _matched_formats(name)),
                }
                for name in (replaces or [current_name])
            ],
        },
        "candidate": {"edition": candidate_edition, "formats": candidate},
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


#: Trackers the sample current torrents announce to; the page names the
#: source of a release it did not download by its tracker host.
SAMPLE_TRACKERS = (
    "https://tracker.alpha-sample.org:443/announce",
    "https://bt.beta-sample.net/announce",
    "udp://open.gamma-sample.io:6969/announce",
)

#: Current size as a share of the upgrade's, per sample in turn.
CURRENT_SIZE_RATIOS = (0.62, 1.04, 0.55, 0.9, 1.12, 0.97, 0.7)


def _torrent_ids(index: int) -> tuple[str, str]:
    base = MOVIE_ID_BASE + index * 16
    return uuid.UUID(int=base + 3).hex, uuid.UUID(int=base + 4).hex


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
                edition_override_active, created_at, updated_at)
               values (?, ?, ?, ?, ?, ?, ?, ?, 'GROUP', 'CURRENT', datetime('now'),
                       ?, ?, ?, 0, datetime('now'), datetime('now'))""",
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
        # The current size comes from the release's torrent, so each sample
        # gets one. The ratio varies, so the size change reads both ways.
        torrent_id, link_id = _torrent_ids(index)
        current_size = round(sample["size"] * CURRENT_SIZE_RATIOS[index % len(CURRENT_SIZE_RATIOS)])
        connection.execute(
            """insert or replace into torrents
               (id, info_hash, name, total_size, first_seen_at, last_seen_at, archive_state,
                tracker_summary, metadata, created_at, updated_at)
               values (?, ?, ?, ?, datetime('now'), datetime('now'), 'NOT_ARCHIVED', ?, ?,
                       datetime('now'), datetime('now'))""",
            (torrent_id, uuid.UUID(torrent_id).hex[:40].ljust(40, "0"), sample["folder"], current_size,
             json.dumps({"tracker": SAMPLE_TRACKERS[index % len(SAMPLE_TRACKERS)]}), json.dumps({MARKER: True})),
        )
        connection.execute(
            """insert or replace into movie_release_torrents
               (id, movie_release_id, torrent_id, association_type, created_at)
               values (?, ?, ?, 'ATTACHED', datetime('now'))""",
            (link_id, release_id, torrent_id),
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


#: Fixed id base for the sample torrents attached to borrowed show releases.
SHOW_TORRENT_ID_BASE = 0x3D1EB000
#: Current size of a borrowed show release, so the show samples show a size
#: change as the movie samples do.
SHOW_CURRENT_SIZE = 60 * 1024**3


def _show_torrent_ids(release_id: str) -> tuple[str, str]:
    """Stable ids per borrowed release, so re-seeding replaces rather than duplicates."""

    offset = uuid.UUID(release_id).int % 0xFFF * 4
    return uuid.UUID(int=SHOW_TORRENT_ID_BASE + offset).hex, uuid.UUID(int=SHOW_TORRENT_ID_BASE + offset + 1).hex


def _seed_show_sizes(connection: sqlite3.Connection, release_ids: set[str]) -> None:
    """Give each borrowed show release a sample torrent, only when it has none.

    The current size is read from a release's torrents. A release that already
    has a real one keeps it untouched.
    """

    for release_id in release_ids:
        has_torrent = connection.execute(
            "select 1 from show_release_torrents where replace(show_release_id, '-', '') = ? limit 1",
            (uuid.UUID(release_id).hex,),
        ).fetchone()
        if has_torrent:
            continue
        torrent_id, link_id = _show_torrent_ids(release_id)
        connection.execute(
            """insert or replace into torrents
               (id, info_hash, name, total_size, first_seen_at, last_seen_at, archive_state,
                tracker_summary, metadata, created_at, updated_at)
               values (?, ?, ?, ?, datetime('now'), datetime('now'), 'NOT_ARCHIVED', ?, ?,
                       datetime('now'), datetime('now'))""",
            (torrent_id, torrent_id[:40].ljust(40, "0"), f"Sample torrent ({MARKER})", SHOW_CURRENT_SIZE,
             json.dumps({"tracker": SAMPLE_TRACKERS[0]}), json.dumps({MARKER: True})),
        )
        connection.execute(
            """insert or replace into show_release_torrents
               (id, show_release_id, torrent_id, association_type, created_at)
               values (?, ?, ?, 'ATTACHED', datetime('now'))""",
            (link_id, release_id, torrent_id),
        )


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
                comparison_snapshot, incoming_release_ids, search_results,
                last_searched_at, created_at, updated_at)
               values (?, ?, 'SHOWS', NULL, ?,
                       ?, ?, ?, ?,
                       ?, ?, ?, NULL,
                       ?, ?, ?, ?,
                       ?, ?, ?, datetime('now', '-2 days'),
                       ?, ?, ?, ?,
                       ?, '[]', '[]',
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
    _seed_show_sizes(connection, {target[3] for target in targets[: len(SAMPLES)]})
    for sample, movie_id, movie_release_id in _seed_movies(connection):
        candidate_id = uuid.uuid4().hex
        snapshot, added, current_score, score = _comparison(
            sample["folder"],
            sample["candidate"],
            None,
            sample["current_quality"],
            sample["edition"],
            sample["edition"],
        )
        connection.execute(
            """insert into upgrade_candidates
               (id, target_key, media_type, movie_id, season_id,
                current_release_ids, current_release_name, current_quality, current_custom_format_score,
                indexer_id, indexer_name, guid, download_url,
                title, quality, custom_format_score, score_delta,
                size, seeders, leechers, published_at,
                upgrade_types, added_custom_formats, execution_eligible, execution_block_reason,
                comparison_snapshot, incoming_release_ids, search_results,
                last_searched_at, created_at, updated_at)
               values (?, ?, 'MOVIES', ?, NULL,
                       ?, ?, ?, ?,
                       ?, ?, ?, NULL,
                       ?, ?, ?, ?,
                       ?, ?, ?, datetime('now', '-1 day'),
                       ?, ?, ?, ?,
                       ?, '[]', '[]',
                       datetime('now'), datetime('now'), datetime('now'))""",
            (
                candidate_id,
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
        if sample.get("state"):
            _start_sample(connection, MOVIE_SAMPLES.index(sample), candidate_id, sample)
        inserted += 1

    connection.commit()
    print(f"inserted {inserted} sample upgrade candidates")


def _start_sample(connection: sqlite3.Connection, index: int, candidate_id: str, sample: dict) -> None:
    """Put a sample into its execution state, with a download to report on.

    Progress is read from the client's observation of the incoming torrent, so
    a sample with progress gets both. Their ids are fixed per sample.
    """

    client = connection.execute("select id from download_clients limit 1").fetchone()
    torrent_id = None
    if client is not None and sample.get("progress") is not None:
        base = MOVIE_ID_BASE + index * 16
        torrent_id, observation_id = uuid.UUID(int=base + 5).hex, uuid.UUID(int=base + 6).hex
        connection.execute(
            """insert or replace into torrents
               (id, info_hash, name, total_size, first_seen_at, last_seen_at, archive_state,
                tracker_summary, metadata, created_at, updated_at)
               values (?, ?, ?, ?, datetime('now'), datetime('now'), 'NOT_ARCHIVED', '{}', ?,
                       datetime('now'), datetime('now'))""",
            (torrent_id, torrent_id[:40].ljust(40, "0"), sample["candidate"], sample["size"],
             json.dumps({MARKER: True})),
        )
        progress = float(sample["progress"])
        left = round(sample["size"] * (1 - progress))
        # A swarm this size moves roughly this fast; finished downloads idle.
        speed = sample["seeders"] * 1_200_000 if progress < 1 else 0
        connection.execute(
            """insert or replace into torrent_client_observations
               (id, torrent_id, download_client_id, state, progress, amount_left, download_speed, eta_seconds,
                tags, is_present, first_seen_at, last_seen_at)
               values (?, ?, ?, ?, ?, ?, ?, ?, '[]', 1, datetime('now'), datetime('now'))""",
            (observation_id, torrent_id, client[0], "downloading" if progress < 1 else "uploading", progress,
             left, speed, left // speed if speed else None),
        )
    connection.execute(
        """update upgrade_candidates
           set execution_state = ?, execution_error = ?, submitted_at = datetime('now', '-3 hours'),
               selected_download_client_id = ?, selected_download_client_name = 'qBittorrent',
               torrent_id = ?,
               validated_at = case when ? = 'cleanup_required' then datetime('now', '-1 hour') end
           where id = ?""",
        (sample["state"], sample.get("error"), client[0] if client else None, torrent_id, sample["state"],
         candidate_id),
    )


def clear() -> None:
    connection = sqlite3.connect(DB)
    cursor = connection.execute(
        "delete from upgrade_candidates where comparison_snapshot like ?", (f'%"{MARKER}"%',)
    )
    # The sample movie exists only to give the movie candidate a real title and
    # folder, so it leaves with it. Its folder on disk is never touched.
    for index in range(len(MOVIE_SAMPLES)):
        movie_id, release_id, directory_id = _ids(index)
        torrent_id, link_id = _torrent_ids(index)
        connection.execute("delete from movie_release_torrents where id = ?", (link_id,))
        connection.execute("delete from torrents where id = ?", (torrent_id,))
        connection.execute("delete from media_directories where id = ?", (directory_id,))
        connection.execute("delete from movie_releases where id = ?", (release_id,))
        connection.execute("delete from movies where id = ?", (movie_id,))
    # Sample torrents (current sizes, and downloads of started samples) carry
    # the marker; real torrents never do.
    connection.execute(
        "delete from torrent_client_observations where torrent_id in (select id from torrents where metadata like ?)",
        (f'%"{MARKER}"%',),
    )
    connection.execute(
        "delete from show_release_torrents where torrent_id in (select id from torrents where metadata like ?)",
        (f'%"{MARKER}"%',),
    )
    connection.execute("delete from torrents where metadata like ?", (f'%"{MARKER}"%',))
    connection.commit()
    print(f"removed {cursor.rowcount} sample upgrade candidates")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--clear", action="store_true", help="remove the samples again")
    args = parser.parse_args()
    clear() if args.clear else seed()
