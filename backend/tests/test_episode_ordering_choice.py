"""Picking the episode ordering that matches the files on disk."""

from app.services.episode_ordering import (
    OrderingCandidate,
    best_ordering,
    score_ordering,
    total_observed,
)

DEFAULT = OrderingCandidate(id=None, name="TMDB default", episode_counts={1: 16, 2: 15})
SEGMENTS = OrderingCandidate(id="dvd", name="DVD order", episode_counts={1: 16, 2: 60})
SHORT = OrderingCandidate(id="short", name="Broadcast", episode_counts={1: 8})


def test_scoring_counts_only_numbers_the_ordering_contains():
    observed = {2: {1, 15, 16, 60}}
    assert score_ordering(observed, DEFAULT) == 2
    assert score_ordering(observed, SEGMENTS) == 4


def test_a_season_the_ordering_does_not_have_fits_nothing():
    assert score_ordering({9: {1, 2}}, SHORT) == 0


def test_episode_zero_and_negatives_never_fit():
    # Specials are season 0, not episode 0, so a zero is a parse artefact.
    assert score_ordering({1: {0, -1, 1}}, DEFAULT) == 1


def test_the_super_friends_case_switches_to_the_segment_ordering():
    # 15 broadcast hours on the TMDB record, 60 segments in the filenames.
    observed = {2: set(range(1, 61))}
    chosen = best_ordering(observed, [DEFAULT, SEGMENTS], current_id=None)
    assert chosen is not None and chosen.id == "dvd"


def test_an_ordering_that_already_fits_everything_is_left_alone():
    observed = {1: {1, 2, 3}, 2: {1, 2}}
    assert best_ordering(observed, [DEFAULT, SEGMENTS], current_id=None) is None


def test_a_tie_keeps_the_current_ordering():
    # Switching renumbers a library, so an equal fit is not a reason to.
    tie = OrderingCandidate(id="other", name="Other", episode_counts={1: 16, 2: 15})
    assert best_ordering({1: {1, 2}}, [DEFAULT, tie], current_id=None) is None


def test_the_margin_can_require_a_bigger_improvement():
    observed = {2: {1, 16}}
    assert best_ordering(observed, [DEFAULT, SEGMENTS], current_id=None) is not None
    assert best_ordering(observed, [DEFAULT, SEGMENTS], current_id=None, margin=5) is None


def test_a_hand_picked_ordering_is_compared_against_rather_than_ignored():
    # The person chose SEGMENTS. Nothing here fits better, so it stays.
    observed = {2: set(range(1, 61))}
    assert best_ordering(observed, [DEFAULT, SEGMENTS], current_id="dvd") is None


def test_the_shorter_of_two_equal_fits_wins():
    # Both cover the files; the one that invents fewer episodes is the safer
    # reading of what the release group meant.
    lean = OrderingCandidate(id="lean", name="Lean", episode_counts={1: 20})
    padded = OrderingCandidate(id="padded", name="Padded", episode_counts={1: 200})
    chosen = best_ordering({1: {1, 20}}, [SHORT, padded, lean], current_id="short")
    assert chosen is not None and chosen.id == "lean"


def test_nothing_observed_means_nothing_to_decide():
    assert best_ordering({}, [DEFAULT, SEGMENTS], current_id=None) is None
    assert best_ordering({1: set()}, [DEFAULT, SEGMENTS], current_id=None) is None


def test_no_candidates_is_not_an_error():
    assert best_ordering({1: {1}}, [], current_id=None) is None


def test_total_observed_counts_every_season():
    assert total_observed({1: {1, 2}, 2: {5}}) == 3
