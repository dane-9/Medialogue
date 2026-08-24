"""Resolving a release's languages, following Radarr's AggregateLanguages.

Parsing the release title is only the weakest of several signals. The order of
precedence matters and is not obvious, so it lives here as one pure function
rather than being spread through the search service.
"""

from __future__ import annotations

import re

from app.parser.iso_languages import resolve_language
from app.parser.release_parser import UNKNOWN_LANGUAGE, parse_release_name

#: Parsed from German "DL"/"ML" tags; resolved to the work's original language.
ORIGINAL_LANGUAGE = "Original"

_MULTI = re.compile(r"(?<![a-z0-9])(multi|multilang|multisub|dual[\W_]?audio)(?![a-z0-9])", re.IGNORECASE)


def has_multiple_languages(title: str) -> bool:
    """Whether a release title advertises itself as multi-language."""
    return bool(_MULTI.search(title or ""))


def _only_unknown(languages: tuple[str, ...]) -> bool:
    return not languages or (len(languages) == 1 and languages[0] == UNKNOWN_LANGUAGE)


def aggregate_languages(
    *,
    release_title: str,
    parsed_languages: tuple[str, ...],
    indexer_languages: tuple[str, ...] = (),
    indexer_multi_languages: tuple[str, ...] = (),
    media_title: str | None = None,
    original_language: str | None = None,
) -> tuple[str, ...]:
    """Decide what languages a release actually offers.

    The precedence, highest first:

    1. What the indexer declared. The tracker knows what it is serving, so this
       replaces the parse outright rather than merging with it.
    2. What the title declared, minus any language contributed only by the
       media's own title — "The French Connection" is not a French release.
    3. The indexer's multi-language setting, when the title carries a MULTI tag
       and nothing better is known.
    4. The work's original language from TMDB, when nothing above resolved.

    Finally, a parsed "Original" (German DL/ML tags) resolves to the original
    language, which is what that tag actually means.
    """
    fallback = resolve_language(original_language)

    if indexer_languages:
        languages = list(indexer_languages)
    else:
        languages = list(parsed_languages)

        # A language word inside the media's own title is part of the name, not
        # a statement about this release's audio.
        if media_title:
            from_title = parse_release_name(media_title).languages
            contributed = [name for name in from_title if name != UNKNOWN_LANGUAGE]
            if contributed:
                stripped = _remove_media_title(release_title, media_title)
                still_present = parse_release_name(stripped).languages if stripped else ()
                for name in contributed:
                    if name in languages and name not in still_present:
                        languages.remove(name)

    if _only_unknown(tuple(languages)) and indexer_multi_languages and has_multiple_languages(release_title):
        languages = list(indexer_multi_languages)
    elif indexer_multi_languages and has_multiple_languages(release_title):
        for name in indexer_multi_languages:
            if name not in languages:
                languages.append(name)

    if _only_unknown(tuple(languages)) and fallback:
        languages = [fallback]

    if ORIGINAL_LANGUAGE in languages:
        languages.remove(ORIGINAL_LANGUAGE)
        if fallback and fallback not in languages:
            languages.append(fallback)
        else:
            languages.append(UNKNOWN_LANGUAGE)

    if not languages:
        languages = [UNKNOWN_LANGUAGE]
    return tuple(languages)


def _remove_media_title(release_title: str, media_title: str) -> str:
    """Drop the media title from the release name, so only its tags remain."""
    normalized = re.sub(r"[._]+", " ", release_title).strip()
    needle = re.sub(r"[._]+", " ", media_title).strip()
    if not needle:
        return normalized
    index = normalized.lower().find(needle.lower())
    if index < 0:
        return normalized
    return (normalized[:index] + normalized[index + len(needle):]).strip()
