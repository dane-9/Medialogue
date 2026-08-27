"""Problem types shared across persistence and API presentation.

One list of the conditions Medialogue actually raises, so the taxonomy cannot
drift from the code that files them. It had: the classifier and the action list
both branched on reasons nothing emitted any more, which made the set look
richer than it was and hid that most Problems end the same way.
"""

RETIRED_PROBLEM_REASONS = frozenset(
    {
        "EPISODE_MAPPING_UNRESOLVED",
        "EPISODE_CONTAINER_MISMATCH",
        "LOW_CONFIDENCE_MATCH",
        "TMDB_SHOW_METADATA_UNAVAILABLE",
        "TORRENT_REMOVED_EXTERNALLY",
        "QBIT_REMOVE_FAILED",
        "ACTIVE_RELEASE_LIMIT_REACHED",
        "PATH_MAPPING_FAILED",
        "SHOW_DIRECTORY_RECONCILIATION_FAILED",
        "TMDB_IDENTITY_UNRESOLVED",
        "TMDB_SHOW_IDENTITY_UNRESOLVED",
        "TORRENT_SHOW_CONTAINER_REQUIRED",
    }
)

PROBLEM_PRESENTATIONS: dict[str, tuple[str, str, str]] = {
    "PLEX_IDENTITY_MISMATCH": (
        "Identity conflict",
        "Plex and Medialogue disagree about this media",
        "Plex associates these files with different title or episode information. The files are untouched, but the two libraries will not agree until one identity is corrected.",
    ),
    "DUPLICATE_PHYSICAL_RELEASE": (
        "Same-edition movie copies",
        "Multiple copies of the same movie edition were found",
        "These folders belong to the same movie and currently have the same edition. Correct the editions if they are different versions, or remove the copy you do not want.",
    ),
    "DUPLICATE_EPISODE_RELEASE": (
        "Multiple episode files",
        "More than one file is attached to this episode",
        "The episode maps to multiple files that still exist on disk. Choose the preferred file, remove the unwanted copy yourself, then recheck.",
    ),
    "TORRENT_PATH_NOT_FOUND": (
        "Download unavailable",
        "Medialogue cannot access the completed download",
        "qBittorrent reports the download as complete, but its local path is missing, inaccessible, or cannot be resolved. Check the download location and any path mapping.",
    ),
    "ROOT_UNREACHABLE": (
        "Media folder unavailable",
        "Medialogue cannot access this media folder",
        "The configured media folder could not be read. Its library entries are kept, but scans cannot confirm their files until the folder is available again.",
    ),
    "AMBIGUOUS_REPLACEMENT_TARGET": (
        "Replacement target unclear",
        "Medialogue cannot tell which movie release to replace",
        "The new download matches a movie with more than one missing release, so there is no safe replacement target. Select or clean up the intended release before attaching it.",
    ),
}

PROBLEM_LABELS: dict[str, str] = {
    reason: presentation[0] for reason, presentation in PROBLEM_PRESENTATIONS.items()
}

MOVIE_MATCH_REASONS = frozenset({"PLEX_IDENTITY_MISMATCH"})
SHOW_MATCH_REASONS = frozenset({"PLEX_IDENTITY_MISMATCH"})

DUPLICATE_REASONS = frozenset({"DUPLICATE_PHYSICAL_RELEASE", "DUPLICATE_EPISODE_RELEASE"})

CONFIG_REASONS = frozenset({"ROOT_UNREACHABLE", "TORRENT_PATH_NOT_FOUND"})


def problem_label(reason: str) -> str:
    """A short description of the condition, falling back to the raw reason."""
    return PROBLEM_LABELS.get(reason, reason.replace("_", " ").capitalize())


def problem_title(reason: str) -> str:
    """Plain-language heading for the Problems list and detail view."""
    presentation = PROBLEM_PRESENTATIONS.get(reason)
    return presentation[1] if presentation else problem_label(reason)


def problem_description(reason: str, fallback: str) -> str:
    """Explain the consequence of a condition without exposing diagnostics."""
    presentation = PROBLEM_PRESENTATIONS.get(reason)
    return presentation[2] if presentation else fallback
