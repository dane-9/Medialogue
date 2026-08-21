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
    # Apostrophes are normally possessive/contraction punctuation. Removing
    # them rather than replacing them with a space makes ``Emperor's`` and
    # ``Emperors`` comparable while the later punctuation pass handles the
    # surrounding title safely.
    normalized = normalized.replace("’", "").replace("'", "").replace("`", "")
    normalized = re.sub(r"[^a-z0-9]+", " ", normalized)
    return re.sub(r"\s+", " ", normalized).strip()


def identity_titles_equivalent(left: str | None, right: str | None) -> bool:
    left_normalized = normalize_identity_title(left)
    right_normalized = normalize_identity_title(right)
    return bool(left_normalized and right_normalized and left_normalized == right_normalized)
