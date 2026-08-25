"""Choosing the episode ordering that fits the files on disk.

TMDB publishes more than one numbering for many shows: the broadcast seasons on
the record itself, plus alternate "episode groups" for DVD order, absolute
order, or a network's own run. A release group numbers its files by whichever
one it used, and when that is not the one Medialogue is reading, every file past
the end of a season references an episode that does not exist.

Super Friends is the plain example. TMDB lists 15 episodes for its second
season, one per broadcast hour; the files number all 60 segments inside those
hours. The first fifteen map, and the remaining thirty-four are unresolvable
against an ordering nobody chose deliberately.

The scoring here is deliberately dumb: it asks how many of the episode numbers
actually observed on disk exist in each candidate ordering. That is the only
question that matters, it needs no heuristics about naming, and it is wrong in
an obvious way rather than a subtle one when it is wrong.
"""

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class OrderingCandidate:
    """One numbering, as episode counts per season number."""

    #: None is TMDB's own season structure rather than a named episode group.
    id: str | None
    name: str
    #: season number -> how many episodes that season contains
    episode_counts: dict[int, int]


def score_ordering(observed: dict[int, set[int]], candidate: OrderingCandidate) -> int:
    """How many observed episode numbers exist in this ordering.

    Observed numbers are season number -> the episode numbers files claim. A
    number fits when the ordering's season is at least that long. Seasons the
    ordering does not have contribute nothing, which is what makes an ordering
    with fewer seasons lose to one that covers the files.
    """
    fitted = 0
    for season_number, numbers in observed.items():
        available = candidate.episode_counts.get(season_number, 0)
        fitted += sum(1 for number in numbers if 1 <= number <= available)
    return fitted


def total_observed(observed: dict[int, set[int]]) -> int:
    return sum(len(numbers) for numbers in observed.values())


def best_ordering(
    observed: dict[int, set[int]],
    candidates: list[OrderingCandidate],
    *,
    current_id: str | None,
    margin: int = 1,
) -> OrderingCandidate | None:
    """The ordering worth switching to, or None to leave things alone.

    Switching renumbers a library, so it takes more than a tie: a candidate has
    to fit at least `margin` more of the observed numbers than the ordering in
    use. Ties keep the current one, and a current ordering that already fits
    everything is never replaced.
    """
    if not candidates or not total_observed(observed):
        return None

    current = next((item for item in candidates if item.id == current_id), None)
    current_score = score_ordering(observed, current) if current is not None else 0
    if current is not None and current_score == total_observed(observed):
        return None

    ranked = sorted(
        (item for item in candidates if item.id != current_id),
        # Most fitted first; a shorter ordering breaks ties, because inventing
        # fewer episodes for the same coverage is the more conservative fit.
        key=lambda item: (-score_ordering(observed, item), sum(item.episode_counts.values())),
    )
    if not ranked:
        return None
    best = ranked[0]
    return best if score_ordering(observed, best) >= current_score + margin else None


async def auto_select_episode_ordering(
    db,
    show,
    observed: dict[int, set[int]],
    *,
    client_factory=None,
) -> str | None:
    """Switch a show to the ordering its files are numbered by.

    Only for shows nobody has chosen an ordering for. A hand-picked ordering is
    a decision, and a scan finding files that disagree with it is not grounds to
    overrule the person who made it.

    Returns the name of the ordering adopted, or None when the current one is
    left alone.
    """
    # Resolved through the tmdb service module rather than imported directly,
    # so the client this uses is the same one everything else in the app uses,
    # including whatever a test has substituted for it.
    from app.services import tmdb as tmdb_service
    from app.services.tmdb import get_tmdb_configuration, sync_show_metadata

    if show.tmdb_id is None or show.tmdb_episode_group_id is not None:
        return None
    if not total_observed(observed):
        return None

    configuration = await get_tmdb_configuration(db)
    if configuration is None or not configuration.enabled or not configuration.api_key:
        return None
    client = (client_factory or tmdb_service.TMDBClient)(configuration.api_key)
    try:
        details = await client.get_show(show.tmdb_id)
        default = OrderingCandidate(
            id=None,
            name="TMDB default",
            episode_counts={season.season_number: season.episode_count for season in details.seasons},
        )
        # Nothing to gain from listing alternates when the record itself fits.
        if score_ordering(observed, default) == total_observed(observed):
            return None

        candidates = [default]
        for summary in await client.list_episode_groups(show.tmdb_id):
            try:
                group = await client.get_episode_group(summary.id)
            except Exception:
                # One unreadable group must not stop the others being weighed.
                continue
            if not group.seasons:
                continue
            candidates.append(OrderingCandidate(
                id=summary.id,
                name=summary.name,
                episode_counts={number: len(episodes) for number, _, episodes in group.seasons},
            ))
    except Exception:
        # Ordering detection is an improvement, never a reason for a scan to
        # fail: an unreachable TMDB leaves the show numbered as it was.
        return None
    finally:
        await client.close()

    chosen = best_ordering(observed, candidates, current_id=show.tmdb_episode_group_id)
    if chosen is None or chosen.id is None:
        return None

    show.tmdb_episode_group_id = chosen.id
    show.revision += 1
    await sync_show_metadata(db, show, client_factory=client_factory)
    return chosen.name
