from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum


class ReleaseState(StrEnum):
    CURRENT = "current"
    MISSING = "missing"
    REPLACED = "replaced"
    REMOVED = "removed"
    CONFLICT = "conflict"
    DUPLICATE = "duplicate"


class PlexState(StrEnum):
    MATCHED = "matched"
    PENDING = "pending"
    NOT_FOUND = "not_found"
    CONFLICT = "conflict"
    UNAVAILABLE = "unavailable"
    UNKNOWN = "unknown"


class DecisionKind(StrEnum):
    NO_CHANGE = "no_change"
    INCOMING = "incoming"
    ATTACH_NEW = "attach_new"
    REAPPEARED = "reappeared"
    REPLACEMENT = "replacement"
    DUPLICATE = "duplicate"
    CONFLICT = "conflict"
    IGNORED = "ignored"
    PROBLEM = "problem"


class PresenceDecision(StrEnum):
    PRESENT = "present"
    DEGRADED = "degraded"
    MISSING = "missing"
    ROOT_UNAVAILABLE = "root_unavailable"


@dataclass(frozen=True, slots=True)
class ExistingRelease:
    release_id: str
    resolved_path: str
    state: ReleaseState
    path_exists: bool
    edition: str | None = None
    # A release can retain more than one historical directory.  Callers may
    # provide the path that was most recently observed; the engine only ever
    # treats a physically existing path as a duplicate candidate.
    root_available: bool = True


@dataclass(frozen=True, slots=True)
class CandidateObservation:
    resolved_path: str | None
    inside_allowed_root: bool
    identity_matches: bool
    download_complete: bool
    plex_state: PlexState = PlexState.UNKNOWN
    confidence: float = 0.0
    edition: str | None = None
    mapping_failed: bool = False
    # Completion is authoritative qBit state.  These fields are optional so
    # lightweight callers/tests can continue to use the pure engine without
    # performing a filesystem walk themselves.
    directory_exists: bool | None = None
    filename_identity_matches: bool = True
    root_available: bool = True
    parser_identity: str | None = None
    preferred_replacement_release_id: str | None = None


@dataclass(frozen=True, slots=True)
class Decision:
    kind: DecisionKind
    reason_code: str
    old_release_id: str | None = None
    events: tuple[str, ...] = field(default_factory=tuple)
    details: dict[str, object] = field(default_factory=dict)
