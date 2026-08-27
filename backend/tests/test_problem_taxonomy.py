"""The Problem taxonomy: one list of conditions, one place they are named."""

from app.core.problem_types import (
    CONFIG_REASONS,
    DUPLICATE_REASONS,
    MOVIE_MATCH_REASONS,
    PROBLEM_LABELS,
    PROBLEM_PRESENTATIONS,
    SHOW_MATCH_REASONS,
    problem_description,
    problem_label,
    problem_title,
)
from app.models.domain import ProblemWorkflow, classify_problem_workflow


def test_every_reason_the_taxonomy_routes_is_a_reason_it_names():
    # A reason that can be classified but not labelled would reach the UI as a
    # raw enum string, which is the failure this registry exists to prevent.
    routed = DUPLICATE_REASONS | MOVIE_MATCH_REASONS | SHOW_MATCH_REASONS | CONFIG_REASONS
    assert routed <= set(PROBLEM_LABELS)


def test_every_named_reason_classifies_to_a_real_workflow():
    for reason in PROBLEM_LABELS:
        assert isinstance(classify_problem_workflow(reason), ProblemWorkflow)


def test_labels_describe_the_condition_rather_than_the_fix():
    assert problem_label("DUPLICATE_PHYSICAL_RELEASE") == "Same-edition movie copies"
    assert problem_label("ROOT_UNREACHABLE") == "Media folder unavailable"


def test_every_problem_has_distinct_plain_language_presentation():
    assert set(PROBLEM_PRESENTATIONS) == set(PROBLEM_LABELS)
    for reason, (label, title, description) in PROBLEM_PRESENTATIONS.items():
        assert label and title and description
        assert problem_title(reason) == title
        assert problem_description(reason, "technical fallback") == description
        assert title != description
        assert reason not in title
        assert reason not in description


def test_an_unknown_reason_is_still_readable():
    # Better a humanised enum than a blank badge if a new condition ships
    # before it is named here.
    assert problem_label("SOMETHING_NEW_ENTIRELY") == "Something new entirely"


def test_retired_reasons_are_not_part_of_the_taxonomy():
    from app.core.problem_types import RETIRED_PROBLEM_REASONS

    assert not (RETIRED_PROBLEM_REASONS & set(PROBLEM_LABELS))
