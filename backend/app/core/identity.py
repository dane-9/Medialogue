from __future__ import annotations

import re
import unicodedata


def normalize_identity_title(value: str | None) -> str:
    """Normalize a media title for identity comparison, not display.

    The normalizer intentionally treats punctuation-only differences as equal
    and canonicalizes ``&`` to ``and``. This keeps external services from
    disagreeing over titles such as ``Oliver & Company`` vs ``Oliver and
    Company`` or ``Mr.`` vs ``Mr`` while preserving meaningful words/numbers.
    """

    if not value:
        return ""
    normalized = unicodedata.normalize("NFKD", value).casefold()
    normalized = "".join(char for char in normalized if not unicodedata.combining(char))
    normalized = normalized.replace("&", " and ")
    normalized = normalized.replace("’", "").replace("'", "").replace("`", "")
    normalized = re.sub(r"[^a-z0-9]+", " ", normalized)
    return re.sub(r"\s+", " ", normalized).strip()


_ROMAN_NUMERALS = {
    name: str(number)
    for number, name in enumerate(
        ("i", "ii", "iii", "iv", "v", "vi", "vii", "viii", "ix", "x", "xi", "xii", "xiii", "xiv", "xv", "xvi", "xvii", "xviii", "xix", "xx"),
        start=1,
    )
}


def comparable_release_title(value: str | None) -> str:
    """A title in the form used to tell whether a release names it.

    Release names drop what a library title spells out: "Part 2" becomes "2" and
    "II" becomes "2". Both sides go through this, so "I, Robot" still only
    matches itself.
    """

    words = [_ROMAN_NUMERALS.get(word, word) for word in normalize_identity_title(value).split()]
    return " ".join(
        word for index, word in enumerate(words)
        if not (word == "part" and index + 1 < len(words) and words[index + 1].isdigit())
    )


def release_title_matches(left: str | None, right: str | None) -> bool:
    left_comparable = comparable_release_title(left)
    return bool(left_comparable) and left_comparable == comparable_release_title(right)


def identity_titles_equivalent(left: str | None, right: str | None) -> bool:
    left_normalized = normalize_identity_title(left)
    right_normalized = normalize_identity_title(right)
    return bool(left_normalized and right_normalized and left_normalized == right_normalized)
