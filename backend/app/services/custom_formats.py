"""Deterministic, explainable Custom Format evaluation.

This module intentionally owns only matching.  Scores live in quality
profiles, so one Custom Format can be reused with different signed scores.
The evaluator accepts either parser objects or application-owned dictionaries,
which keeps it useful to API and persistence layers without importing models.
"""

from __future__ import annotations

import re
import uuid
from collections import defaultdict
from dataclasses import dataclass, field
from typing import Any, Iterable, Mapping, Sequence

from app.parser import ReleaseParseResult, parse_release


SCHEMA_VERSION = 1


def _new_id() -> str:
    return str(uuid.uuid4())


def _fold(value: Any) -> str:
    return str(value).strip().casefold()


def _type_name(value: Any) -> str:
    return re.sub(r"[^a-z0-9]+", "_", _fold(value)).strip("_")


_TYPE_ALIASES = {
    "release_title": "release_title",
    "title": "release_title",
    "release_group": "release_group",
    "group": "release_group",
    "quality": "quality",
    "quality_modifier": "quality_modifier",
    "modifier": "quality_modifier",
    "resolution": "resolution",
    "source": "source",
    "edition": "edition",
    "language": "language",
    "indexer": "indexer",
    "tracker": "indexer",
    "web_provider": "web_provider",
    "provider": "web_provider",
    "video_codec": "video_codec",
    "audio_codec": "audio_codec",
    "audio_channels": "audio_channels",
    "channels": "audio_channels",
    "hdr_type": "hdr_type",
    "hdr": "hdr_type",
    "release_attribute": "release_attribute",
    "attribute": "release_attribute",
}


@dataclass(frozen=True, slots=True)
class FormatCondition:
    """One editor condition card."""

    type: str
    value: Any = None
    name: str | None = None
    pattern: str | None = None
    required: bool = False
    negate: bool = False
    case_sensitive: bool = False
    id: str = field(default_factory=_new_id)
    group: str | None = None

    @property
    def condition_type(self) -> str:
        return _TYPE_ALIASES.get(_type_name(self.type), _type_name(self.type))

    @property
    def expected(self) -> Any:
        if self.pattern is not None:
            return self.pattern
        if self.value is not None:
            return self.value
        return self.name

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> "FormatCondition":
        # Definitions imported from the app/API commonly wrap conditions in
        # `definition`; accepting both shapes makes round trips painless.
        return cls(
            type=str(value.get("type", value.get("condition_type", ""))),
            value=value.get("value"),
            name=value.get("name"),
            pattern=value.get("pattern", value.get("regex")),
            required=bool(value.get("required", False)),
            negate=bool(value.get("negate", value.get("negated", False))),
            case_sensitive=bool(value.get("case_sensitive", False)),
            id=str(value.get("id") or _new_id()),
            group=value.get("group"),
        )

    def to_dict(self) -> dict[str, Any]:
        value: dict[str, Any] = {
            "id": self.id,
            "name": self.name,
            "type": self.condition_type,
            "value": self.value,
            "required": self.required,
            "negate": self.negate,
            "case_sensitive": self.case_sensitive,
        }
        if self.pattern is not None:
            value["pattern"] = self.pattern
        if self.group is not None:
            value["group"] = self.group
        return value


# Short alias used by clients that call these simply Condition.
Condition = FormatCondition


@dataclass(frozen=True, slots=True)
class CustomFormat:
    name: str
    conditions: tuple[FormatCondition, ...] = ()
    id: str = field(default_factory=_new_id)
    description: str | None = None
    media_scope: str = "both"
    enabled: bool = True
    schema_version: int = SCHEMA_VERSION

    def __post_init__(self) -> None:
        object.__setattr__(
            self,
            "conditions",
            tuple(
                condition
                if isinstance(condition, FormatCondition)
                else FormatCondition.from_dict(condition)
                for condition in self.conditions
            ),
        )

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> "CustomFormat":
        definition = value.get("definition", value.get("condition_definition"))
        if isinstance(definition, Mapping):
            source = definition
        else:
            source = value
        conditions = source.get("conditions", value.get("conditions", ()))
        return cls(
            name=str(value.get("name", source.get("name", "Unnamed"))),
            conditions=tuple(FormatCondition.from_dict(item) for item in conditions),
            id=str(value.get("id") or _new_id()),
            description=value.get("description"),
            media_scope=str(value.get("media_scope", "both")),
            enabled=bool(value.get("enabled", True)),
            schema_version=int(value.get("schema_version", source.get("schema_version", SCHEMA_VERSION))),
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "id": self.id,
            "name": self.name,
            "description": self.description,
            "media_scope": self.media_scope,
            "enabled": self.enabled,
            "conditions": [condition.to_dict() for condition in self.conditions],
        }

    # Application-owned import/export aliases.
    export = to_dict


@dataclass(frozen=True, slots=True)
class ConditionResult:
    condition_id: str
    condition_type: str
    name: str | None
    matched: bool
    effective_result: bool
    required: bool
    negated: bool
    evidence: Any = None
    expected: Any = None
    reason: str | None = None
    group: str | None = None
    regex_match: str | None = None

    @property
    def passed(self) -> bool:
        return self.effective_result

    @property
    def type(self) -> str:
        return self.condition_type

    @property
    def negate(self) -> bool:
        return self.negated

    def to_dict(self) -> dict[str, Any]:
        return {
            "condition_id": self.condition_id,
            "condition_type": self.condition_type,
            "name": self.name,
            "matched": self.matched,
            "effective_result": self.effective_result,
            "required": self.required,
            "negated": self.negated,
            "evidence": self.evidence,
            "expected": self.expected,
            "reason": self.reason,
            "group": self.group,
            "regex_match": self.regex_match,
        }


@dataclass(frozen=True, slots=True)
class CustomFormatEvaluation:
    custom_format_id: str
    custom_format_name: str
    matched: bool
    conditions: tuple[ConditionResult, ...]
    score: int = 0
    group_results: dict[str, bool] = field(default_factory=dict)
    error: str | None = None

    @property
    def matched_conditions(self) -> tuple[ConditionResult, ...]:
        return tuple(item for item in self.conditions if item.effective_result)

    @property
    def name(self) -> str:
        return self.custom_format_name

    @property
    def failed_conditions(self) -> tuple[ConditionResult, ...]:
        return tuple(item for item in self.conditions if not item.effective_result)

    @property
    def score_contribution(self) -> int:
        return self.score if self.matched else 0

    def explain(self) -> dict[str, Any]:
        return self.to_dict()

    def to_dict(self) -> dict[str, Any]:
        return {
            "custom_format_id": self.custom_format_id,
            "custom_format_name": self.custom_format_name,
            "matched": self.matched,
            "score": self.score_contribution,
            "configured_score": self.score,
            "conditions": [condition.to_dict() for condition in self.conditions],
            "group_results": dict(self.group_results),
            "error": self.error,
        }


@dataclass(frozen=True, slots=True)
class CustomFormatSetEvaluation:
    parsed: ReleaseParseResult
    formats: tuple[CustomFormatEvaluation, ...]
    total_score: int

    @property
    def evaluations(self) -> tuple[CustomFormatEvaluation, ...]:
        return self.formats

    @property
    def score(self) -> int:
        return self.total_score

    @property
    def matched_formats(self) -> tuple[CustomFormatEvaluation, ...]:
        return tuple(item for item in self.formats if item.matched)

    def to_dict(self) -> dict[str, Any]:
        return {
            "parsed": self.parsed.to_dict(),
            "formats": [item.to_dict() for item in self.formats],
            "total_score": self.total_score,
        }

    def explain(self) -> dict[str, Any]:
        return self.to_dict()


def _get(obj: Any, *path: str, default: Any = None) -> Any:
    current = obj
    for key in path:
        if current is None:
            return default
        if isinstance(current, Mapping):
            current = current.get(key, default)
        else:
            current = getattr(current, key, default)
    return current


def _parse_input(value: Any) -> ReleaseParseResult:
    if isinstance(value, ReleaseParseResult):
        return value
    if isinstance(value, str):
        return parse_release(value)
    # A parser snapshot is accepted for API/persistence callers.  Re-parsing
    # its raw name preserves all parser behavior and avoids partial snapshots
    # silently changing matching semantics.
    if isinstance(value, Mapping):
        raw = value.get("raw_name", value.get("raw", value.get("name", "")))
        if raw:
            return parse_release(str(raw))
    raise TypeError("release must be a release name, ReleaseParseResult, or parser snapshot")


def _as_values(value: Any) -> tuple[Any, ...]:
    if value is None:
        return ()
    if isinstance(value, (list, tuple, set, frozenset)):
        return tuple(value)
    return (value,)


def _structured_evidence(
    condition: FormatCondition,
    parsed: ReleaseParseResult,
    context: Mapping[str, Any],
) -> tuple[tuple[Any, ...], str]:
    ctype = condition.condition_type
    if ctype == "release_title":
        return (parsed.raw_name,), "raw release name"
    if ctype == "release_group":
        return (parsed.release_group,), "release group"
    if ctype == "quality":
        return ((parsed.quality.canonical,) if parsed.quality.canonical else ()), "quality"
    if ctype == "quality_modifier":
        return ((parsed.quality.modifier,) if parsed.quality.modifier else ()), "quality modifier"
    if ctype == "resolution":
        return ((parsed.quality.resolution,) if parsed.quality.resolution else ()), "resolution"
    if ctype == "source":
        return ((parsed.quality.source,) if parsed.quality.source else ()), "source"
    if ctype == "edition":
        return ((parsed.edition,) if parsed.edition else ()), "edition"
    if ctype == "language":
        values = tuple(parsed.languages) + tuple(context.get("languages", ()))
        return values, "language"
    if ctype == "indexer":
        value = context.get("indexer", context.get("tracker"))
        return ((value,) if value is not None else ()), "indexer"
    if ctype == "web_provider":
        return ((parsed.provider,) if parsed.provider else ()), "WEB provider"
    if ctype == "video_codec":
        return ((parsed.video.codec,) if parsed.video.codec else ()), "video codec"
    if ctype == "audio_codec":
        values: list[Any] = []
        if parsed.audio.codec:
            values.append(parsed.audio.codec)
        if parsed.audio.atmos:
            values.append("Atmos")
        if parsed.audio.dts_x:
            values.append("DTS:X")
        return tuple(values), "audio codec"
    if ctype == "audio_channels":
        return ((parsed.audio.channels,) if parsed.audio.channels else ()), "audio channels"
    if ctype == "hdr_type":
        return parsed.hdr.values, "HDR type"
    if ctype == "release_attribute":
        return parsed.attributes.values, "release attribute"
    # Unknown condition types can still be used against explicit context
    # fields, making extension conditions safe and deterministic.
    value = context.get(ctype)
    return _as_values(value), ctype


def _matches_expected(
    condition: FormatCondition,
    evidence: Sequence[Any],
) -> tuple[bool, str | None, str | None]:
    expected = condition.expected
    if expected is None or expected == "":
        return False, None, "condition has no value/pattern"
    ctype = condition.condition_type
    flags = 0 if condition.case_sensitive else re.IGNORECASE
    if ctype in {"release_title", "release_group"}:
        try:
            regex = re.compile(str(expected), flags)
        except re.error as exc:
            return False, None, f"invalid regex: {exc}"
        for item in evidence:
            match = regex.search(str(item))
            if match:
                return True, str(item), match.group(0)
        return False, str(evidence[0]) if evidence else None, None

    expected_values = _as_values(expected)
    for actual in evidence:
        if actual is None:
            continue
        for wanted in expected_values:
            if _fold(actual) == _fold(wanted):
                return True, actual, None
            # HDR aliases and release attributes have a few intentional
            # shorthand spellings that should remain structured conditions.
            aliases = {
                "dv": "dolby vision",
                "dolbyvision": "dolby vision",
                "webdl": "web-dl",
                "webrip": "webrip",
            }
            if aliases.get(_fold(actual), _fold(actual)) == aliases.get(_fold(wanted), _fold(wanted)):
                return True, actual, None
    return False, evidence[0] if evidence else None, None


def _evaluate_condition(
    condition: FormatCondition,
    parsed: ReleaseParseResult,
    context: Mapping[str, Any],
) -> ConditionResult:
    evidence, evidence_label = _structured_evidence(condition, parsed, context)
    underlying, evidence_value, regex_match = _matches_expected(condition, evidence)
    effective = not underlying if condition.negate else underlying
    validation_error = regex_match if regex_match and regex_match.startswith("invalid regex:") else None
    if validation_error:
        regex_match = None
        reason = validation_error
    elif underlying:
        reason = f"matched {evidence_label}"
    elif evidence:
        reason = f"no match in {evidence_label}"
    else:
        reason = f"{evidence_label} not present"
    if condition.negate:
        reason = ("negated: " + reason)
    return ConditionResult(
        condition_id=condition.id,
        condition_type=condition.condition_type,
        name=condition.name,
        matched=underlying,
        effective_result=effective,
        required=condition.required,
        negated=condition.negate,
        evidence=evidence_value,
        expected=condition.expected,
        reason=reason,
        group=condition.group,
        regex_match=regex_match,
    )


def _group_key(condition: FormatCondition) -> str:
    # Explicit groups are useful when an editor wants two independent cards of
    # the same type; otherwise all same-type cards form one OR/required group.
    return condition.group or condition.condition_type


def evaluate_custom_format(
    custom_format: CustomFormat | Mapping[str, Any],
    release: ReleaseParseResult | str | Mapping[str, Any],
    *,
    context: Mapping[str, Any] | None = None,
    score: int = 0,
    profile_score: int | None = None,
) -> CustomFormatEvaluation:
    """Evaluate one format and return every condition's explanation.

    Within a group, optional conditions are ORed.  Required conditions are
    mandatory; if a group has both required and optional cards, all required
    cards and at least one optional card must pass.  Distinct groups are ANDed.
    This makes the grouping behavior independent of card ordering.
    """

    fmt = custom_format if isinstance(custom_format, CustomFormat) else CustomFormat.from_dict(custom_format)
    if profile_score is not None:
        score = int(profile_score)
    parsed = _parse_input(release)
    context = context or {}
    condition_results = tuple(_evaluate_condition(item, parsed, context) for item in fmt.conditions)
    groups: dict[str, list[ConditionResult]] = defaultdict(list)
    for condition, result in zip(fmt.conditions, condition_results):
        groups[_group_key(condition)].append(result)

    group_results: dict[str, bool] = {}
    for key, results in groups.items():
        required = [item for item in results if item.required]
        optional = [item for item in results if not item.required]
        group_match = all(item.effective_result for item in required)
        if optional:
            group_match = group_match and any(item.effective_result for item in optional)
        group_results[key] = group_match
    matched = bool(condition_results) and all(group_results.values())
    return CustomFormatEvaluation(
        custom_format_id=fmt.id,
        custom_format_name=fmt.name,
        matched=matched,
        conditions=condition_results,
        score=int(score),
        group_results=group_results,
    )


def evaluate_condition(
    condition: FormatCondition | Mapping[str, Any],
    release: ReleaseParseResult | str | Mapping[str, Any],
    *,
    context: Mapping[str, Any] | None = None,
) -> ConditionResult:
    """Evaluate one condition card for parser/test tooling."""

    item = condition if isinstance(condition, FormatCondition) else FormatCondition.from_dict(condition)
    return _evaluate_condition(item, _parse_input(release), context or {})


def _score_for(
    fmt: CustomFormat,
    profile_scores: Mapping[str, int] | None,
    overrides: Mapping[str, int] | None,
) -> int:
    profile_scores = profile_scores or {}
    overrides = overrides or {}
    # Per-title overrides intentionally replace, rather than add to, a profile
    # score for a specific format as specified by the architecture.
    if fmt.id in overrides:
        return int(overrides[fmt.id])
    if fmt.name in overrides:
        return int(overrides[fmt.name])
    if fmt.id in profile_scores:
        return int(profile_scores[fmt.id])
    if fmt.name in profile_scores:
        return int(profile_scores[fmt.name])
    return 0


def evaluate_custom_formats(
    custom_formats: Iterable[CustomFormat | Mapping[str, Any]],
    release: ReleaseParseResult | str | Mapping[str, Any],
    *,
    profile_scores: Mapping[str, int] | None = None,
    scores: Mapping[str, int] | None = None,
    score_overrides: Mapping[str, int] | None = None,
    context: Mapping[str, Any] | None = None,
    include_disabled: bool = False,
) -> CustomFormatSetEvaluation:
    """Evaluate all enabled formats and sum only matched contributions."""

    parsed = _parse_input(release)
    profile_scores = profile_scores if profile_scores is not None else scores
    evaluations: list[CustomFormatEvaluation] = []
    for item in custom_formats:
        fmt = item if isinstance(item, CustomFormat) else CustomFormat.from_dict(item)
        if not fmt.enabled and not include_disabled:
            continue
        evaluations.append(
            evaluate_custom_format(
                fmt,
                parsed,
                context=context,
                score=_score_for(fmt, profile_scores, score_overrides),
            )
        )
    total = sum(item.score_contribution for item in evaluations)
    return CustomFormatSetEvaluation(parsed=parsed, formats=tuple(evaluations), total_score=total)


def validate_condition(condition: FormatCondition | Mapping[str, Any]) -> tuple[str, ...]:
    """Return backend validation errors without mutating the definition."""

    item = condition if isinstance(condition, FormatCondition) else FormatCondition.from_dict(condition)
    errors: list[str] = []
    if not item.condition_type:
        errors.append("condition type is required")
    if item.expected is None or item.expected == "":
        errors.append("condition value/pattern is required")
    if item.condition_type in {"release_title", "release_group"} and item.expected:
        try:
            re.compile(str(item.expected), 0 if item.case_sensitive else re.IGNORECASE)
        except re.error as exc:
            errors.append(f"invalid regex: {exc}")
    return tuple(errors)


def validate_custom_format(custom_format: CustomFormat | Mapping[str, Any]) -> tuple[str, ...]:
    fmt = custom_format if isinstance(custom_format, CustomFormat) else CustomFormat.from_dict(custom_format)
    errors: list[str] = []
    if not fmt.name.strip():
        errors.append("custom format name is required")
    for index, condition in enumerate(fmt.conditions):
        errors.extend(f"condition {index}: {error}" for error in validate_condition(condition))
    return tuple(errors)


def export_custom_format(custom_format: CustomFormat | Mapping[str, Any]) -> dict[str, Any]:
    fmt = custom_format if isinstance(custom_format, CustomFormat) else CustomFormat.from_dict(custom_format)
    return fmt.to_dict()


def import_custom_format(value: Mapping[str, Any]) -> CustomFormat:
    return CustomFormat.from_dict(value)


class CustomFormatEngine:
    """Small service facade for callers that prefer an object API."""

    def evaluate(
        self,
        custom_format: CustomFormat | Mapping[str, Any],
        release: ReleaseParseResult | str | Mapping[str, Any],
        **kwargs: Any,
    ) -> CustomFormatEvaluation:
        return evaluate_custom_format(custom_format, release, **kwargs)

    def evaluate_all(
        self,
        custom_formats: Iterable[CustomFormat | Mapping[str, Any]],
        release: ReleaseParseResult | str | Mapping[str, Any],
        **kwargs: Any,
    ) -> CustomFormatSetEvaluation:
        return evaluate_custom_formats(custom_formats, release, **kwargs)


# Concise aliases for service/API callers.
evaluate = evaluate_custom_format
evaluate_all = evaluate_custom_formats


__all__ = [
    "SCHEMA_VERSION",
    "Condition",
    "ConditionResult",
    "CustomFormat",
    "CustomFormatEvaluation",
    "CustomFormatEngine",
    "CustomFormatSetEvaluation",
    "FormatCondition",
    "evaluate",
    "evaluate_all",
    "evaluate_condition",
    "evaluate_custom_format",
    "evaluate_custom_formats",
    "export_custom_format",
    "import_custom_format",
    "validate_condition",
    "validate_custom_format",
]
