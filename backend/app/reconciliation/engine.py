from __future__ import annotations

from collections.abc import Sequence

from .types import (
    CandidateObservation,
    Decision,
    DecisionKind,
    ExistingRelease,
    PresenceDecision,
    ReleaseState,
)


class ReconciliationEngine:
    """Pure decision engine; callers persist its result transactionally.

    Integrations feed observations here instead of mutating Movie/Show rows.
    The engine deliberately performs no filesystem writes.
    """

    def __init__(self, *, minimum_auto_match_confidence: float = 0.90) -> None:
        if not 0 <= minimum_auto_match_confidence <= 1:
            raise ValueError("minimum_auto_match_confidence must be between 0 and 1")
        self.minimum_auto_match_confidence = minimum_auto_match_confidence

    @staticmethod
    def assess_presence(
        *, root_available: bool, path_exists: bool, consecutive_failures: int, threshold: int = 3
    ) -> PresenceDecision:
        if threshold < 1:
            raise ValueError("threshold must be positive")
        if not root_available:
            return PresenceDecision.ROOT_UNAVAILABLE
        if path_exists:
            return PresenceDecision.PRESENT
        if consecutive_failures < threshold:
            return PresenceDecision.DEGRADED
        return PresenceDecision.MISSING

    def reconcile_candidate(
        self,
        candidate: CandidateObservation,
        existing: Sequence[ExistingRelease],
    ) -> Decision:
        # An inaccessible root is a single root-level health result, never a
        # per-release missing conclusion.  The filesystem observer is the
        # authority for root availability, while qBit observations may still
        # be retained for recovery/history.
        if not candidate.root_available:
            return Decision(DecisionKind.PROBLEM, "ROOT_UNREACHABLE")
        if candidate.mapping_failed:
            return Decision(DecisionKind.PROBLEM, "PATH_MAPPING_FAILED")
        if not candidate.inside_allowed_root:
            return Decision(DecisionKind.IGNORED, "OUTSIDE_MANAGED_ROOT")
        if not candidate.resolved_path:
            return Decision(DecisionKind.PROBLEM, "TORRENT_PATH_NOT_FOUND")
        if candidate.download_complete:
            if candidate.directory_exists is False:
                return Decision(DecisionKind.PROBLEM, "TORRENT_PATH_NOT_FOUND")
            if not candidate.filename_identity_matches:
                return Decision(DecisionKind.PROBLEM, "LOW_CONFIDENCE_MATCH")
        if not candidate.identity_matches or candidate.confidence < self.minimum_auto_match_confidence:
            return Decision(
                DecisionKind.PROBLEM,
                "LOW_CONFIDENCE_MATCH",
                details={"confidence": candidate.confidence},
            )
        # Plex is a presence/path observer, not an identity authority. Movie
        # identity is established by TMDB/manual matching, so Plex metadata
        # differences must never block attachment or replacement.
        if not candidate.download_complete:
            return Decision(DecisionKind.INCOMING, "INCOMING_DOWNLOAD", events=("torrent.incoming",))

        same_path = next((item for item in existing if item.resolved_path == candidate.resolved_path), None)
        if same_path is not None:
            if self._state_is(same_path.state, ReleaseState.MISSING) or not same_path.path_exists:
                return Decision(
                    DecisionKind.REAPPEARED,
                    "MEDIA_REAPPEARED",
                    old_release_id=same_path.release_id,
                    events=("media.reappeared",),
                )
            return Decision(DecisionKind.NO_CHANGE, "ALREADY_ATTACHED", old_release_id=same_path.release_id)

        physical_active = [
            item
            for item in existing
            if item.path_exists and self._state_in(item.state, {ReleaseState.CURRENT, ReleaseState.DUPLICATE})
        ]
        # A physically present release in the same edition slot is always a
        # duplicate. This check comes before replacement selection so the app
        # never silently discards a real on-disk duplicate.
        conflicting = next(
            (item for item in physical_active if self._same_edition_slot(item.edition, candidate.edition)),
            None,
        )
        if conflicting is not None:
            return Decision(
                DecisionKind.DUPLICATE,
                "DUPLICATE_PHYSICAL_RELEASE",
                old_release_id=conflicting.release_id,
                events=("release.duplicate",),
            )

        missing_candidates = [
            item
            for item in existing
            if self._state_in(item.state, {ReleaseState.CURRENT, ReleaseState.MISSING}) and not item.path_exists
        ]
        if missing_candidates:
            preferred = None
            if candidate.preferred_replacement_release_id:
                preferred = next(
                    (item for item in missing_candidates if item.release_id == candidate.preferred_replacement_release_id),
                    None,
                )
            if preferred is None:
                same_edition_missing = [
                    item for item in missing_candidates if self._same_edition_slot(item.edition, candidate.edition)
                ]
                if len(same_edition_missing) == 1:
                    preferred = same_edition_missing[0]
            if preferred is None and len(missing_candidates) == 1:
                preferred = missing_candidates[0]
            if preferred is None:
                return Decision(
                    DecisionKind.PROBLEM,
                    "AMBIGUOUS_REPLACEMENT_TARGET",
                    details={"candidate_release_ids": [item.release_id for item in missing_candidates]},
                )
            # Replacement identity is title-based; an edition change is explicitly valid.
            return Decision(
                DecisionKind.REPLACEMENT,
                "REPLACEMENT_COMMITTED",
                old_release_id=preferred.release_id,
                events=("release.replaced", "media.present"),
                details={"previous_edition": preferred.edition, "new_edition": candidate.edition},
            )

        if physical_active:
            if len(physical_active) >= 3:
                return Decision(DecisionKind.PROBLEM, "ACTIVE_RELEASE_LIMIT_REACHED")
            return Decision(DecisionKind.ATTACH_NEW, "NEW_EDITION", events=("media.attached",))

        return Decision(DecisionKind.ATTACH_NEW, "NEW_LIBRARY_ITEM", events=("media.attached",))

    @staticmethod
    def _same_edition_slot(left: str | None, right: str | None) -> bool:
        normalize = lambda value: (value or "").strip().casefold()
        return normalize(left) == normalize(right)

    @staticmethod
    def _state_is(value: object, expected: ReleaseState) -> bool:
        return value is expected or getattr(value, "value", value) == expected.value

    @classmethod
    def _state_in(cls, value: object, expected: set[ReleaseState]) -> bool:
        return any(cls._state_is(value, item) for item in expected)
