from __future__ import annotations


STANDARD_EDITION_LABEL = "Theatrical Cut"


def edition_slot(value: str | None) -> str:
    """Return the identity slot used when comparing physical movie editions."""

    normalized = (value or "").strip().casefold()
    return "" if normalized in {"", "theatrical cut"} else normalized


def same_edition(left: str | None, right: str | None) -> bool:
    return edition_slot(left) == edition_slot(right)


def edition_label(value: str | None) -> str:
    return (value or "").strip() or STANDARD_EDITION_LABEL
