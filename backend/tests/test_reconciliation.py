from app.reconciliation.engine import ReconciliationEngine
from app.reconciliation.path_mapping import RemotePathMapping, is_inside_root, resolve_reported_path
from app.reconciliation.types import (
    CandidateObservation,
    DecisionKind,
    ExistingRelease,
    PlexState,
    PresenceDecision,
    ReleaseState,
)


def candidate(**changes):
    values = {
        "resolved_path": "/media/movies/Inception-new",
        "inside_allowed_root": True,
        "identity_matches": True,
        "download_complete": True,
        "plex_state": PlexState.MATCHED,
        "confidence": 0.98,
        "edition": None,
    }
    values.update(changes)
    return CandidateObservation(**values)


def test_root_outage_does_not_mark_each_item_missing():
    result = ReconciliationEngine.assess_presence(
        root_available=False, path_exists=False, consecutive_failures=100
    )
    assert result is PresenceDecision.ROOT_UNAVAILABLE


def test_root_recovery_restores_presence_without_replaying_item_missing():
    """A root outage is global state, not evidence that every path vanished."""

    outage = ReconciliationEngine.assess_presence(
        root_available=False, path_exists=False, consecutive_failures=100
    )
    recovered = ReconciliationEngine.assess_presence(
        root_available=True, path_exists=True, consecutive_failures=0
    )

    assert outage is PresenceDecision.ROOT_UNAVAILABLE
    assert recovered is PresenceDecision.PRESENT


def test_missing_requires_grace_threshold():
    assert ReconciliationEngine.assess_presence(
        root_available=True, path_exists=False, consecutive_failures=2, threshold=3
    ) is PresenceDecision.DEGRADED
    assert ReconciliationEngine.assess_presence(
        root_available=True, path_exists=False, consecutive_failures=3, threshold=3
    ) is PresenceDecision.MISSING


def test_inception_replacement_can_change_edition():
    old = ExistingRelease("old", "/media/movies/Inception-old", ReleaseState.MISSING, False, None)
    result = ReconciliationEngine().reconcile_candidate(
        candidate(edition="Director's Cut"), [old]
    )
    assert result.kind is DecisionKind.REPLACEMENT
    assert result.old_release_id == "old"
    assert result.details["previous_edition"] is None


def test_inception_replacement_flow_waits_for_qbit_completion_then_commits():
    """The missing release stays historical until a completed candidate is verified."""

    engine = ReconciliationEngine()
    old = ExistingRelease(
        "inception-1080",
        "/media/movies/Inception-2010-1080p",
        ReleaseState.MISSING,
        False,
        None,
    )

    incoming = engine.reconcile_candidate(
        candidate(
            resolved_path="/media/movies/Inception-2010-2160p",
            download_complete=False,
            plex_state=PlexState.PENDING,
        ),
        [old],
    )
    committed = engine.reconcile_candidate(
        candidate(
            resolved_path="/media/movies/Inception-2010-2160p",
            download_complete=True,
            plex_state=PlexState.PENDING,
            edition="Director's Cut",
        ),
        [old],
    )

    assert incoming.kind is DecisionKind.INCOMING
    assert incoming.reason_code == "INCOMING_DOWNLOAD"
    assert incoming.events == ("torrent.incoming",)
    assert committed.kind is DecisionKind.REPLACEMENT
    assert committed.reason_code == "REPLACEMENT_COMMITTED"
    assert committed.old_release_id == old.release_id
    assert committed.events == ("release.replaced", "media.present")
    assert committed.details == {
        "previous_edition": None,
        "new_edition": "Director's Cut",
    }


def test_media_gone_qbit_still_present_is_incoming_not_attachment():
    """A completed flag from qBit is the authority for attachment, not path hints."""

    old = ExistingRelease("old", "/media/movies/Inception-old", ReleaseState.MISSING, False, None)
    result = ReconciliationEngine().reconcile_candidate(
        candidate(
            resolved_path="/media/movies/Inception-new",
            download_complete=False,
            plex_state=PlexState.MATCHED,
        ),
        [old],
    )

    assert result.kind is DecisionKind.INCOMING
    assert result.reason_code == "INCOMING_DOWNLOAD"
    assert result.old_release_id is None


def test_qbit_removed_but_media_present_remains_a_local_no_change():
    """Loss of qBit observation must not make an existing physical file missing."""

    existing = ExistingRelease(
        "old",
        "/media/movies/Inception-2010",
        ReleaseState.CURRENT,
        True,
        None,
    )
    # There is deliberately no qBit observation in CandidateObservation. A
    # local re-observation of the exact path therefore remains attached.
    result = ReconciliationEngine().reconcile_candidate(
        candidate(resolved_path=existing.resolved_path), [existing]
    )

    assert result.kind is DecisionKind.NO_CHANGE
    assert result.reason_code == "ALREADY_ATTACHED"
    assert result.old_release_id == existing.release_id
    assert result.events == ()


def test_replacement_has_no_time_window_and_can_happen_months_later():
    """A missing release remains eligible regardless of elapsed wall-clock time."""

    old = ExistingRelease(
        "old",
        "/media/movies/Inception-2010-old",
        ReleaseState.MISSING,
        False,
        "Theatrical Cut",
    )
    result = ReconciliationEngine().reconcile_candidate(
        candidate(
            resolved_path="/media/movies/Inception-2010-new",
            edition="Extended Cut",
        ),
        [old],
    )

    assert result.kind is DecisionKind.REPLACEMENT
    assert result.details["previous_edition"] == "Theatrical Cut"
    assert result.details["new_edition"] == "Extended Cut"


def test_present_same_edition_is_duplicate_and_never_auto_deleted():
    old = ExistingRelease("old", "/media/movies/Inception-old", ReleaseState.CURRENT, True, None)
    result = ReconciliationEngine().reconcile_candidate(candidate(), [old])
    assert result.kind is DecisionKind.DUPLICATE
    assert result.reason_code == "DUPLICATE_PHYSICAL_RELEASE"
    assert result.events == ("release.duplicate",)


def test_duplicate_requires_both_physical_paths_to_exist():
    old = ExistingRelease("old", "/media/movies/Inception-old", ReleaseState.CURRENT, True, None)
    duplicate = ReconciliationEngine().reconcile_candidate(candidate(), [old])

    old_path_gone = ExistingRelease(
        "old", "/media/movies/Inception-old", ReleaseState.CURRENT, False, None
    )
    replacement = ReconciliationEngine().reconcile_candidate(candidate(), [old_path_gone])

    assert duplicate.kind is DecisionKind.DUPLICATE
    assert replacement.kind is DecisionKind.REPLACEMENT
    assert replacement.reason_code == "REPLACEMENT_COMMITTED"


def test_different_present_edition_attaches_as_valid_slot():
    old = ExistingRelease(
        "old", "/media/movies/Inception-imax", ReleaseState.CURRENT, True, "IMAX"
    )
    result = ReconciliationEngine().reconcile_candidate(candidate(edition="Extended Cut"), [old])
    assert result.kind is DecisionKind.ATTACH_NEW


def test_download_must_complete_before_attachment():
    result = ReconciliationEngine().reconcile_candidate(candidate(download_complete=False), [])
    assert result.kind is DecisionKind.INCOMING


def test_plex_metadata_conflict_does_not_block_tmdb_backed_attachment():
    result = ReconciliationEngine().reconcile_candidate(candidate(plex_state=PlexState.CONFLICT), [])
    assert result.kind is DecisionKind.ATTACH_NEW
    assert result.reason_code == "NEW_LIBRARY_ITEM"


def test_plex_pending_does_not_block_high_confidence_local_match():
    result = ReconciliationEngine().reconcile_candidate(candidate(plex_state=PlexState.PENDING), [])
    assert result.kind is DecisionKind.ATTACH_NEW


def test_plex_unavailable_is_not_an_identity_conflict():
    result = ReconciliationEngine().reconcile_candidate(
        candidate(plex_state=PlexState.UNAVAILABLE), []
    )

    assert result.kind is DecisionKind.ATTACH_NEW
    assert result.reason_code == "NEW_LIBRARY_ITEM"


def test_exact_path_reappearance_is_automatic():
    old = ExistingRelease(
        "old", "/media/movies/Inception-new", ReleaseState.MISSING, False, None
    )
    result = ReconciliationEngine().reconcile_candidate(candidate(), [old])
    assert result.kind is DecisionKind.REAPPEARED


def test_different_allowed_path_reappearance_preserves_old_release_identity():
    """A confident match on a new allowed path revives the logical release."""

    old = ExistingRelease(
        "old", "/media/movies/old/Inception", ReleaseState.MISSING, False, None
    )
    result = ReconciliationEngine().reconcile_candidate(
        candidate(resolved_path="/media/movies/new/Inception"), [old]
    )

    # The pure engine records this as a replacement decision; persistence can
    # retain old.resolved_path as history while attaching the new path.
    assert result.kind is DecisionKind.REPLACEMENT
    assert result.old_release_id == old.release_id
    assert result.events == ("release.replaced", "media.present")


def test_repeated_exact_observation_is_event_idempotent():
    existing = ExistingRelease(
        "current", "/media/movies/Inception-new", ReleaseState.CURRENT, True, None
    )
    first = ReconciliationEngine().reconcile_candidate(candidate(), [existing])
    second = ReconciliationEngine().reconcile_candidate(candidate(), [existing])

    assert first.kind is DecisionKind.NO_CHANGE
    assert second.kind is DecisionKind.NO_CHANGE
    assert first.events == second.events == ()


def test_unrelated_torrent_is_ignored():
    result = ReconciliationEngine().reconcile_candidate(candidate(inside_allowed_root=False), [])
    assert result.kind is DecisionKind.IGNORED


def test_remote_path_mapping_uses_longest_component_prefix():
    result = resolve_reported_path(
        "/downloads/movies/Inception/file.mkv",
        [
            RemotePathMapping("broad", "/downloads", "/media"),
            RemotePathMapping("movies", "/downloads/movies", "/media/movies-array"),
        ],
    )
    assert result.reported_path == "/downloads/movies/Inception/file.mkv"
    assert result.resolved_path == "/media/movies-array/Inception/file.mkv"
    assert result.mapping_id == "movies"


def test_root_containment_is_component_safe():
    assert is_inside_root("/media/movies/Film", "/media/movies")
    assert not is_inside_root("/media/movies-evil/Film", "/media/movies")


def test_missing_release_is_replaced_before_adding_another_present_edition():
    """A different present edition must not steal priority from a Missing release."""

    present = ExistingRelease(
        "theatrical",
        "/media/movies/Inception-theatrical",
        ReleaseState.CURRENT,
        True,
        "Theatrical Cut",
    )
    missing = ExistingRelease(
        "extended",
        "/media/movies/Inception-extended-old",
        ReleaseState.MISSING,
        False,
        "Extended Cut",
    )
    result = ReconciliationEngine().reconcile_candidate(
        candidate(
            resolved_path="/media/movies/Inception-directors-new",
            edition="Director's Cut",
        ),
        [present, missing],
    )

    assert result.kind is DecisionKind.REPLACEMENT
    assert result.old_release_id == "extended"
    assert result.details == {
        "previous_edition": "Extended Cut",
        "new_edition": "Director's Cut",
    }


def test_multiple_missing_releases_require_explicit_replacement_target_when_ambiguous():
    missing_one = ExistingRelease(
        "one", "/media/movies/Inception-old-a", ReleaseState.MISSING, False, "IMAX"
    )
    missing_two = ExistingRelease(
        "two", "/media/movies/Inception-old-b", ReleaseState.MISSING, False, "Extended Cut"
    )
    result = ReconciliationEngine().reconcile_candidate(
        candidate(edition="Director's Cut"), [missing_one, missing_two]
    )

    assert result.kind is DecisionKind.PROBLEM
    assert result.reason_code == "AMBIGUOUS_REPLACEMENT_TARGET"
    assert result.details["candidate_release_ids"] == ["one", "two"]


def test_preferred_incoming_replacement_target_resolves_multiple_missing_releases():
    missing_one = ExistingRelease(
        "one", "/media/movies/Inception-old-a", ReleaseState.MISSING, False, "IMAX"
    )
    missing_two = ExistingRelease(
        "two", "/media/movies/Inception-old-b", ReleaseState.MISSING, False, "Extended Cut"
    )
    result = ReconciliationEngine().reconcile_candidate(
        candidate(
            edition="Director's Cut",
            preferred_replacement_release_id="two",
        ),
        [missing_one, missing_two],
    )

    assert result.kind is DecisionKind.REPLACEMENT
    assert result.old_release_id == "two"
