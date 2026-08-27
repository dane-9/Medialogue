"""Mapping ISO language codes and names onto Medialogue's display names.

Indexers report languages inconsistently, ``en``, ``eng``, ``English``,
``english``, and TMDB reports an ISO 639-1 code. Both are resolved here so the
rest of the system only ever deals with one spelling per language.
"""

from __future__ import annotations

#: (ISO 639-1, ISO 639-2/T, display name). The display name is authoritative.
_LANGUAGES: tuple[tuple[str | None, str | None, str], ...] = (
    ("en", "eng", "English"), ("fr", "fra", "French"), ("es", "spa", "Spanish"),
    ("de", "deu", "German"), ("it", "ita", "Italian"), ("ja", "jpn", "Japanese"),
    ("ko", "kor", "Korean"), ("zh", "zho", "Chinese"), ("ru", "rus", "Russian"),
    ("nl", "nld", "Dutch"), ("sv", "swe", "Swedish"), ("no", "nor", "Norwegian"),
    ("da", "dan", "Danish"), ("fi", "fin", "Finnish"), ("pl", "pol", "Polish"),
    ("pt", "por", "Portuguese"), ("hu", "hun", "Hungarian"), ("cs", "ces", "Czech"),
    ("sk", "slk", "Slovak"), ("bg", "bul", "Bulgarian"), ("ro", "ron", "Romanian"),
    ("el", "ell", "Greek"), ("tr", "tur", "Turkish"), ("ar", "ara", "Arabic"),
    ("he", "heb", "Hebrew"), ("hi", "hin", "Hindi"), ("th", "tha", "Thai"),
    ("vi", "vie", "Vietnamese"), ("uk", "ukr", "Ukrainian"), ("fa", "fas", "Persian"),
    ("bn", "ben", "Bengali"), ("lv", "lav", "Latvian"), ("lt", "lit", "Lithuanian"),
    ("et", "est", "Estonian"), ("hr", "hrv", "Croatian"), ("sr", "srp", "Serbian"),
    ("sl", "slv", "Slovenian"), ("is", "isl", "Icelandic"), ("ta", "tam", "Tamil"),
    ("te", "tel", "Telugu"), ("ml", "mal", "Malayalam"), ("kn", "kan", "Kannada"),
    ("mr", "mar", "Marathi"), ("sq", "sqi", "Albanian"), ("af", "afr", "Afrikaans"),
    ("tl", "tgl", "Tagalog"), ("id", "ind", "Indonesian"), ("ms", "msa", "Malay"),
    ("ca", "cat", "Catalan"), ("mn", "mon", "Mongolian"), ("ka", "kat", "Georgian"),
    ("ur", "urd", "Urdu"), ("rm", "roh", "Romansh"), ("la", "lat", "Latin"),
    ("mk", "mkd", "Macedonian"), ("bs", "bos", "Bosnian"), ("hy", "hye", "Armenian"),
    ("az", "aze", "Azerbaijani"), ("kk", "kaz", "Kazakh"), ("uz", "uzb", "Uzbek"),
    ("ne", "nep", "Nepali"), ("si", "sin", "Sinhala"), ("km", "khm", "Khmer"),
    ("my", "mya", "Burmese"), ("sw", "swa", "Swahili"), ("cy", "cym", "Welsh"),
    ("ga", "gle", "Irish"), ("gl", "glg", "Galician"), ("eu", "eus", "Basque"),
)

_VARIANTS: tuple[tuple[str, str], ...] = (
    ("pt-br", "Portuguese (BR)"), ("pt_br", "Portuguese (BR)"),
    ("brazilian", "Portuguese (BR)"), ("brazilian portuguese", "Portuguese (BR)"),
    ("es-mx", "Spanish (Latino)"), ("latino", "Spanish (Latino)"),
    ("spanish latino", "Spanish (Latino)"), ("latin american spanish", "Spanish (Latino)"),
    ("zh-cn", "Chinese"), ("zh-tw", "Chinese"), ("mandarin", "Chinese"),
    ("cantonese", "Chinese"), ("flemish", "Flemish"),
)


def _index() -> dict[str, str]:
    lookup: dict[str, str] = {}
    for alpha2, alpha3, name in _LANGUAGES:
        lookup[name.lower()] = name
        if alpha2:
            lookup[alpha2] = name
        if alpha3:
            lookup[alpha3] = name
    for key, name in _VARIANTS:
        lookup[key] = name
    return lookup


_LOOKUP = _index()


def resolve_language(value: str | None) -> str | None:
    """Resolve one code or name to a display name, or None if unrecognised.

    Accepts ISO 639-1 ("en"), ISO 639-2 ("eng"), an English name ("English"),
    or a regional variant ("pt-BR"), in any case and with surrounding
    punctuation. Unknown values return None rather than a guess, so an
    indexer's private spelling never becomes a fabricated language.
    """
    if not value:
        return None
    key = value.strip().strip("[](){}.,;:").lower()
    if not key:
        return None
    if key in _LOOKUP:
        return _LOOKUP[key]
    base = key.split("(")[0].strip()
    return _LOOKUP.get(base)


def resolve_languages(values: object) -> tuple[str, ...]:
    """Resolve a comma-separated string, or an iterable, into display names."""
    if values is None:
        return ()
    items: list[str] = []
    if isinstance(values, str):
        items = values.split(",")
    elif isinstance(values, (list, tuple, set)):
        items = [str(item) for item in values]
    else:
        return ()

    resolved: list[str] = []
    for item in items:
        name = resolve_language(item)
        if name and name not in resolved:
            resolved.append(name)
    return tuple(resolved)
