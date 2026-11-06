"""Insert sample Replace Media entries, one per state, for looking at the page.

Temporary: the movies, releases, torrents and tracker evidence are invented.
The torrents belong to a download client id that is not configured, so no poll
ever touches them, and their evidence is dated far in the future so it does not
age out of Replace Media's two-hour window. Nothing is written to disk and
nothing can be downloaded. Remove everything again with
`python scripts/seed_sample_replacements.py --clear`.

Also fills in TMDB posters for these samples and for the Upgrade Media samples
(scripts/seed_sample_upgrades.py) when TMDB is configured.
"""

from __future__ import annotations

import argparse
import asyncio
import sys
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))

from sqlalchemy import delete, select  # noqa: E402

from app.db import session as db_session  # noqa: E402
from app.models.domain import (  # noqa: E402
    AssociationType,
    DownloadClient,
    IdentityState,
    MediaDirectory,
    MediaType,
    Movie,
    MovieRelease,
    MovieReleaseTorrent,
    QualityDefinition,
    ReleaseState,
    StorageRoot,
    Torrent,
    TorrentClientObservation,
    TorrentTrackerObservation,
    UpgradeCandidate,
)

MARKER = "sample_replacement"
UPGRADE_MARKER = "sample_upgrade"
#: Fixed ids so re-seeding replaces rather than duplicates.
BASE = 0x5EED_0000_0000_0000_0000_0000_0000
FAKE_CLIENT = uuid.UUID(int=BASE + 0xFFFF)
#: Keeps the invented tracker evidence inside Replace Media's freshness window.
EVIDENCE_AT = datetime.now(timezone.utc) + timedelta(days=365)

def _format_row(name: str, group: str | None, contribution: int) -> dict:
    return {"custom_format_id": str(uuid.uuid5(uuid.NAMESPACE_URL, f"{MARKER}:{name}")), "custom_format_name": name,
            "custom_format_group": group, "matched": True, "contribution": contribution}


#: The Custom Formats behind the sample candidate score (1450), in the shape real
#: comparisons store. The current side is scored live from the release name.
SAMPLE_BREAKDOWN = {
    "candidate": {"formats": [
        _format_row("HEVC", "Video", 300), _format_row("TrueHD Atmos", "Audio", 600), _format_row("HDR10", "Dynamic Range", 250),
        _format_row("Remux", None, 300),
    ]},
}

#: What trackers say when they drop a torrent, so each sample reads differently.
LOST_MESSAGES = (
    "Unregistered torrent",
    "Trumped by a newer encode",
    "Torrent not registered with this tracker",
    "Dupe of an existing upload",
    "Removed: source was retail-incorrect",
    "Unregistered torrent",
    "Torrent has been deleted",
)

SAMPLES = [
    {"state": "needs_search", "title": "Heat", "year": 1995, "release": "Heat.1995.1080p.BluRay.x264-GROUP", "quality": "1080p Encode"},
    {"state": "no_match", "title": "Collateral", "year": 2004, "release": "Collateral.2004.1080p.BluRay.REMUX.AVC.DTS-HD.MA.5.1-GROUP", "quality": "1080p BluRay REMUX"},
    {"state": "found", "title": "Arrival", "year": 2016, "release": "Arrival.2016.2160p.WEB-DL.DDP5.1.HDR.H.265-GROUP", "quality": "2160p WEB-DL",
     "candidate": "Arrival.2016.2160p.UHD.BluRay.REMUX.HDR.HEVC.TrueHD.7.1.Atmos-OTHER", "candidate_quality": "2160p BluRay REMUX"},
    {"state": "downloading", "title": "Sicario", "year": 2015, "release": "Sicario.2015.1080p.BluRay.x264-GROUP", "quality": "1080p Encode",
     "candidate": "Sicario.2015.1080p.BluRay.REMUX.AVC.DTS-HD.MA.5.1-OTHER", "candidate_quality": "1080p BluRay REMUX"},
    {"state": "handoff_attention", "title": "Prisoners", "year": 2013, "release": "Prisoners.2013.1080p.BluRay.x264-GROUP", "quality": "1080p Encode",
     "candidate": "Prisoners.2013.1080p.BluRay.x264-OTHER", "candidate_quality": "1080p Encode",
     "error": "The replacement completed, but its torrent backup is not complete. The current torrent was kept."},
    {"state": "cleanup_required", "title": "Zodiac", "year": 2007, "release": "Zodiac.2007.Directors.Cut.1080p.BluRay.x264-GROUP", "quality": "1080p Encode",
     "candidate": "Zodiac.2007.Directors.Cut.2160p.UHD.BluRay.REMUX.HDR.HEVC.DTS-HD.MA.5.1-OTHER", "candidate_quality": "2160p BluRay REMUX"},
    {"state": "ignored", "title": "Gone Girl", "year": 2014, "release": "Gone.Girl.2014.1080p.BluRay.x264-GROUP", "quality": "1080p Encode"},
]


def _ids(index: int) -> dict[str, uuid.UUID]:
    base = BASE + index * 16
    return {name: uuid.UUID(int=base + offset) for offset, name in enumerate(
        ["movie", "release", "directory", "torrent", "observation", "tracker", "link", "candidate", "replacement",
         "replacement_observation"]
    )}


def _result(index: int, title: str, quality: str, score: int, seeders: int, *, eligible: bool = True,
            reasons: list[str] | None = None) -> dict:
    return {
        "id": str(uuid.UUID(int=BASE + 0x1000 + index)), "indexer_id": None, "indexer_name": "Sample indexer",
        "indexer_priority": 25, "guid": f"sample-replacement-result-{index}", "download_url": None,
        "title": title, "quality": quality, "edition": None, "release_group": title.rsplit("-", 1)[-1],
        "custom_format_score": score, "size": (40 + index * 7) * 1024**3, "seeders": seeders, "leechers": 1,
        "published_at": (datetime.now(timezone.utc) - timedelta(days=30 + index)).isoformat(),
        "eligible": eligible, "rejection_reasons": reasons or [], "upgrade_types": [], "added_custom_formats": [],
        "snapshot": {}, "parser": {},
    }


async def _quality_ids(db) -> dict[str, uuid.UUID]:
    return {row.name: row.id for row in (await db.scalars(select(QualityDefinition))).all()}


async def clear() -> int:
    async with db_session.async_session_factory() as db:
        removed = (await db.execute(
            delete(UpgradeCandidate).where(UpgradeCandidate.comparison_snapshot.like(f'%"{MARKER}"%'))
        )).rowcount or 0
        torrent_ids = [_ids(index)["torrent"] for index in range(len(SAMPLES) + 1)]
        torrent_ids += [_ids(index)["replacement"] for index in range(len(SAMPLES))]
        await db.execute(delete(TorrentTrackerObservation).where(TorrentTrackerObservation.torrent_id.in_(torrent_ids)))
        await db.execute(delete(TorrentClientObservation).where(TorrentClientObservation.torrent_id.in_(torrent_ids)))
        await db.execute(delete(MovieReleaseTorrent).where(MovieReleaseTorrent.torrent_id.in_(torrent_ids)))
        await db.execute(delete(Torrent).where(Torrent.id.in_(torrent_ids)))
        await db.execute(delete(DownloadClient).where(DownloadClient.id == FAKE_CLIENT))
        for index in range(len(SAMPLES)):
            ids = _ids(index)
            await db.execute(delete(MediaDirectory).where(MediaDirectory.id == ids["directory"]))
            await db.execute(delete(MovieRelease).where(MovieRelease.id == ids["release"]))
            await db.execute(delete(Movie).where(Movie.id == ids["movie"]))
        await db.commit()
    return removed


async def _fill_posters() -> int:
    """Best effort: give sample movies (from both seed scripts) TMDB posters."""

    from app.integrations.tmdb import TMDBClient
    from app.services.tmdb import get_tmdb_configuration

    filled = 0
    async with db_session.async_session_factory() as db:
        configuration = await get_tmdb_configuration(db)
        if configuration is None or not configuration.api_key:
            print("TMDB is not configured; samples keep the placeholder poster.")
            return 0
        movies = (await db.scalars(select(Movie).where(
            Movie.poster_ref.is_(None),
            (Movie.overview.like(f"%{MARKER}%")) | (Movie.overview.like(f"%{UPGRADE_MARKER}%")),
        ))).all()
        client = TMDBClient(configuration.api_key)
        try:
            for movie in movies:
                try:
                    matches = await client.search_movie(movie.title, movie.year)
                except Exception as exc:  # noqa: BLE001 - posters are cosmetic
                    print(f"  poster lookup failed for {movie.title}: {exc}")
                    continue
                match = next((item for item in matches if item.poster_path), None)
                if match is not None:
                    # Poster only: the TMDB id may already belong to a real movie.
                    movie.poster_ref = match.poster_path
                    filled += 1
        finally:
            await client.close()
        await db.commit()
    return filled


async def seed() -> None:
    await clear()
    now = datetime.now(timezone.utc)
    async with db_session.async_session_factory() as db:
        root = await db.scalar(select(StorageRoot).where(StorageRoot.media_type == MediaType.MOVIES).limit(1))
        if root is None:
            raise SystemExit("No movies media folder is configured; add one first.")
        qualities = await _quality_ids(db)
        # Runtime-state row only: the client is not in the integration config,
        # so it is never listed, polled or shown.
        db.add(DownloadClient(id=FAKE_CLIENT, health="unknown"))
        await db.flush()

        def torrent_rows(ids, name: str, *, metadata: dict, message: str = "Unregistered torrent",
                         last_worked=None, size: int | None = None) -> list:
            return [
                Torrent(id=ids["torrent"], info_hash=ids["torrent"].hex + "00000000", name=name, total_size=size,
                        metadata_json={MARKER: True, "content_path": None, **metadata},
                        tracker_summary={"state": "unregistered", "tracker": "https://tracker.sample/announce"}),
                TorrentClientObservation(id=ids["observation"], torrent_id=ids["torrent"], download_client_id=FAKE_CLIENT,
                                         state="stalledUP", progress=1, is_present=True,
                                         first_seen_at=now, last_seen_at=EVIDENCE_AT),
                TorrentTrackerObservation(id=ids["tracker"], torrent_id=ids["torrent"], download_client_id=FAKE_CLIENT,
                                          endpoint_key=f"sample-{ids['torrent'].hex[:8]}",
                                          endpoint_display="https://tracker.sample/announce", active=True,
                                          health_state="error", message=message, last_success_at=last_worked,
                                          terminal_confirmed=True, last_checked_at=EVIDENCE_AT),
            ]

        for index, sample in enumerate(SAMPLES):
            ids = _ids(index)
            db.add(Movie(id=ids["movie"], title=sample["title"], sort_title=sample["title"], year=sample["year"],
                         overview=f"Seeded for UI review ({MARKER}).", monitored=True,
                         identity_state=IdentityState.MATCHED))
            db.add(MovieRelease(id=ids["release"], movie_id=ids["movie"], raw_release_name=sample["release"],
                                parsed_title=sample["title"], parsed_year=sample["year"],
                                quality_definition_id=qualities.get(sample["quality"]), release_group="GROUP",
                                release_state=ReleaseState.CURRENT, parse_snapshot={MARKER: True}))
            await db.flush()
            path = f"{root.resolved_root_path}\\{sample['release']}"
            db.add(MediaDirectory(id=ids["directory"], storage_root_id=root.id, reported_path=path, resolved_path=path,
                                  movie_release_id=ids["release"], exists=True))
            metadata: dict = {}
            if sample["state"] == "no_match":
                metadata = {"replacement_last_search": (now - timedelta(hours=3)).isoformat(),
                            "replacement_search_result": "no_match"}
            elif sample["state"] == "ignored":
                metadata = {"replacement_review": {"ignored": True}}
            elif sample.get("candidate"):
                metadata = {"replacement_last_search": (now - timedelta(minutes=40)).isoformat(),
                            "replacement_search_result": "candidate"}
            if sample["state"] == "cleanup_required":
                # A validated replacement retires the lost source, so the
                # queue keeps it visible through the active handoff instead.
                metadata["replacement_candidate_id"] = str(ids["candidate"])
            db.add_all(torrent_rows(ids, sample["release"], metadata=metadata,
                                    message=LOST_MESSAGES[index % len(LOST_MESSAGES)],
                                    last_worked=now - timedelta(days=index + 1, hours=3),
                                    # Lost copies a little smaller or larger than their replacements.
                                    size=(40 + index * 7) * 1024**3 + (index % 3 - 1) * 6 * 1024**3))
            await db.flush()
            db.add(MovieReleaseTorrent(id=ids["link"], movie_release_id=ids["release"], torrent_id=ids["torrent"],
                                       association_type=AssociationType.ATTACHED))

            if sample.get("candidate"):
                state = sample["state"]
                results = [
                    _result(index * 4, sample["candidate"], sample["candidate_quality"], 1450, 42),
                    _result(index * 4 + 1, sample["candidate"].replace("-OTHER", "-THIRD"), sample["candidate_quality"], 1300, 17),
                    _result(index * 4 + 2, sample["release"].replace("-GROUP", "-LOWER").replace("1080p", "720p").replace("2160p", "1080p"),
                            "720p Encode", 200, 120, eligible=False,
                            reasons=["Quality is lower than the current release."]),
                    _result(index * 4 + 3, sample["candidate"].replace("-OTHER", "-FAILED"), sample["candidate_quality"], 1450, 8,
                            eligible=False, reasons=["Blocklisted: qBittorrent reported terminal state 'missingFiles'."]),
                ]
                db.add(UpgradeCandidate(
                    id=ids["candidate"], target_key=f"replace:movie-release:{ids['release']}", media_type=MediaType.MOVIES,
                    movie_id=ids["movie"], current_release_ids=[str(ids["release"])],
                    current_release_name=sample["release"], current_quality=sample["quality"],
                    current_custom_format_score=900, indexer_name="Sample indexer",
                    guid=results[0]["guid"], download_url=None, title=sample["candidate"],
                    quality=sample["candidate_quality"], custom_format_score=1450, score_delta=550,
                    size=results[0]["size"], seeders=42, leechers=1, upgrade_types=["quality"],
                    comparison_snapshot={MARKER: True, **SAMPLE_BREAKDOWN}, search_results=results, last_searched_at=now - timedelta(minutes=40),
                    execution_state=None if state == "found" else state,
                    execution_error=sample.get("error"),
                    submitted_at=None if state == "found" else now - timedelta(hours=2),
                    validated_at=now - timedelta(minutes=10) if state == "cleanup_required" else None,
                    # A download in flight reports live numbers from its torrent.
                    torrent_id=ids["replacement"] if state == "downloading" else None,
                    selected_download_client_id=FAKE_CLIENT if state == "downloading" else None,
                ))
                if state == "downloading":
                    size = results[0]["size"] or 0
                    db.add(Torrent(id=ids["replacement"], info_hash=ids["replacement"].hex + "00000000",
                                   name=sample["candidate"], total_size=size, metadata_json={MARKER: True}))
                    db.add(TorrentClientObservation(
                        id=ids["replacement_observation"], torrent_id=ids["replacement"], download_client_id=FAKE_CLIENT,
                        state="downloading", progress=0.46, download_speed=9_500_000,
                        amount_left=round(size * 0.54), eta_seconds=round(size * 0.54 / 9_500_000),
                        is_present=True, first_seen_at=now, last_seen_at=EVIDENCE_AT,
                    ))

        await db.commit()
    filled = await _fill_posters()
    print(f"inserted {len(SAMPLES)} sample Replace Media entries ({filled} posters filled)")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--clear", action="store_true", help="remove the samples again")
    args = parser.parse_args()
    if args.clear:
        print(f"removed samples ({asyncio.run(clear())} candidates)")
    else:
        asyncio.run(seed())
