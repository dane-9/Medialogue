"""Deterministic, explainable Custom Format evaluation.

This module intentionally owns only matching.  Scores live in quality
profiles, so one Custom Format can be reused with different signed scores.
The evaluator consumes the current parser result or a raw release title.
Persistence mappings are normalized into service objects before evaluation.
"""

from __future__ import annotations

import re
import uuid
from collections import defaultdict
from dataclasses import dataclass, field, replace
from typing import Any, Iterable, Mapping, Sequence

from app.parser import ReleaseParseResult, parse_release


SCHEMA_VERSION = 1


def _new_id() -> str:
    return str(uuid.uuid4())


@dataclass(frozen=True, slots=True)
class FormatCondition:
    """One editor condition card."""

    type: str
    name: str | None = None
    pattern: str | None = None
    required: bool = False
    negate: bool = False
    case_sensitive: bool = False
    id: str = field(default_factory=_new_id)
    group: str | None = None
    score_offset: int = 0

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> "FormatCondition":
        return cls(
            type=str(value.get("type", "")),
            name=value.get("name"),
            pattern=value.get("pattern"),
            required=bool(value.get("required", False)),
            negate=bool(value.get("negate", False)),
            case_sensitive=bool(value.get("case_sensitive", False)),
            id=str(value.get("id") or _new_id()),
            group=value.get("group"),
            score_offset=int(value.get("score_offset", 0) or 0),
        )

    def to_dict(self) -> dict[str, Any]:
        value: dict[str, Any] = {
            "id": self.id,
            "name": self.name,
            "type": self.type,
            "required": self.required,
            "negate": self.negate,
            "case_sensitive": self.case_sensitive,
            "score_offset": self.score_offset,
        }
        if self.pattern is not None:
            value["pattern"] = self.pattern
        if self.group is not None:
            value["group"] = self.group
        return value


@dataclass(frozen=True, slots=True)
class CustomFormat:
    name: str
    conditions: tuple[FormatCondition, ...] = ()
    id: str = field(default_factory=_new_id)
    description: str | None = None
    enabled: bool = True
    supersedes: tuple[str, ...] = ()
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
        definition = value.get("condition_definition")
        if not isinstance(definition, Mapping):
            raise ValueError("custom format mapping requires condition_definition")
        if int(definition["schema_version"]) != SCHEMA_VERSION:
            raise ValueError(f"unsupported Custom Format schema: {definition['schema_version']}")
        conditions = definition["conditions"]
        supersedes = definition["supersedes"]
        if not isinstance(conditions, (list, tuple)):
            raise ValueError("custom format conditions must be a list")
        if not isinstance(supersedes, (list, tuple)):
            raise ValueError("custom format supersedes must be a list")
        return cls(
            name=str(value["name"]),
            conditions=tuple(FormatCondition.from_dict(item) for item in conditions),
            id=str(value.get("id") or _new_id()),
            description=value.get("description"),
            enabled=bool(value["enabled"]),
            supersedes=tuple(str(item) for item in supersedes),
            schema_version=SCHEMA_VERSION,
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "name": self.name,
            "description": self.description,
            "enabled": self.enabled,
            "condition_definition": {
                "schema_version": self.schema_version,
                "supersedes": list(self.supersedes),
                "conditions": [condition.to_dict() for condition in self.conditions],
            },
        }


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
    score_offset: int = 0

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
            "score_offset": self.score_offset,
        }


@dataclass(frozen=True, slots=True)
class CustomFormatEvaluation:
    custom_format_id: str
    custom_format_name: str
    matched: bool
    conditions: tuple[ConditionResult, ...]
    score: int = 0
    score_offset: int = 0
    # Matching and scoring are separate concerns. A format can match for
    # explainability while contributing nothing when the active Quality Profile
    # has not selected it.
    score_applies: bool = True
    group_results: dict[str, bool] = field(default_factory=dict)
    error: str | None = None
    raw_matched: bool | None = None
    suppressed_by: tuple[str, ...] = ()
    suppressed_by_names: tuple[str, ...] = ()

    @property
    def score_contribution(self) -> int:
        return self.score + self.score_offset if self.matched and self.score_applies else 0

    def to_dict(self) -> dict[str, Any]:
        return {
            "custom_format_id": self.custom_format_id,
            "custom_format_name": self.custom_format_name,
            "matched": self.matched,
            "raw_matched": self.matched if self.raw_matched is None else self.raw_matched,
            "suppressed_by": list(self.suppressed_by),
            "suppressed_by_names": list(self.suppressed_by_names),
            "score": self.score_contribution,
            "configured_score": self.score,
            "score_offset": self.score_offset,
            "conditions": [condition.to_dict() for condition in self.conditions],
            "group_results": dict(self.group_results),
            "error": self.error,
        }


@dataclass(frozen=True, slots=True)
class CustomFormatSetEvaluation:
    parsed: ReleaseParseResult
    formats: tuple[CustomFormatEvaluation, ...]
    total_score: int

    def to_dict(self) -> dict[str, Any]:
        return {
            "parsed": self.parsed.to_dict(),
            "formats": [item.to_dict() for item in self.formats],
            "total_score": self.total_score,
        }



def _coerce_release_parse(value: ReleaseParseResult | str) -> ReleaseParseResult:
    if isinstance(value, ReleaseParseResult):
        return value
    if isinstance(value, str):
        return parse_release(value)
    raise TypeError("release must be a release name or ReleaseParseResult")


def _as_tuple(value: Any) -> tuple[Any, ...]:
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
    ctype = condition.type
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
            if parsed.audio.codec:
                values.append(f"{parsed.audio.codec} Atmos")
        if parsed.audio.dts_x:
            values.append("DTS:X")
        if parsed.audio.auro_3d:
            values.append("Auro3D")
        return tuple(values), "audio codec"
    if ctype == "audio_channels":
        return ((parsed.audio.channels,) if parsed.audio.channels else ()), "audio channels"
    if ctype == "hdr_type":
        values = list(parsed.hdr.values)
        for value in tuple(values):
            if str(value).strip().casefold() == "dolby vision":
                values.extend(("DV", "DoVi"))
        return tuple(dict.fromkeys(values)), "HDR type"
    if ctype == "release_attribute":
        return parsed.attributes.values, "release attribute"
    value = context.get(ctype)
    return _as_tuple(value), ctype


def _matches_expected(
    condition: FormatCondition,
    evidence: Sequence[Any],
) -> tuple[bool, str | None, str | None, int | None]:
    expected = condition.pattern
    if expected is None or expected == "":
        return False, None, "condition has no value/pattern", None
    flags = 0 if condition.case_sensitive else re.IGNORECASE
    patterns = _as_tuple(expected)
    for wanted in patterns:
        expression = str(wanted) if condition.pattern is not None else rf"^(?:{re.escape(str(wanted))})$"
        try:
            regex = re.compile(expression, flags)
        except re.error as exc:
            return False, None, f"invalid regex: {exc}", None
        for item in evidence:
            match = regex.search(str(item))
            if match:
                captured_offset: int | None = None
                if "score_offset" in match.re.groupindex:
                    raw_offset = match.group("score_offset")
                    if raw_offset:
                        try:
                            captured_offset = max(-100000, min(100000, int(raw_offset)))
                        except ValueError:
                            captured_offset = None
                return True, str(item), match.group(0), captured_offset
    return False, evidence[0] if evidence else None, None, None


def _evaluate_condition(
    condition: FormatCondition,
    parsed: ReleaseParseResult,
    context: Mapping[str, Any],
) -> ConditionResult:
    evidence, evidence_label = _structured_evidence(condition, parsed, context)
    underlying, evidence_value, regex_match, captured_offset = _matches_expected(condition, evidence)
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
        condition_type=condition.type,
        name=condition.name,
        matched=underlying,
        effective_result=effective,
        required=condition.required,
        negated=condition.negate,
        evidence=evidence_value,
        expected=condition.pattern,
        reason=reason,
        group=condition.group,
        regex_match=regex_match,
        score_offset=captured_offset if captured_offset is not None else condition.score_offset,
    )


def _group_key(condition: FormatCondition) -> str:
    return condition.group or condition.type


def evaluate_custom_format(
    custom_format: CustomFormat,
    release: ReleaseParseResult | str,
    *,
    context: Mapping[str, Any] | None = None,
    score: int = 0,
) -> CustomFormatEvaluation:
    """Evaluate one format and return every condition's explanation.

    Within a group, optional conditions are ORed.  Required conditions are
    mandatory; if a group has both required and optional cards, all required
    cards and at least one optional card must pass.  Distinct groups are ANDed.
    This makes the grouping behavior independent of card ordering.
    """

    fmt = custom_format
    parsed = _coerce_release_parse(release)
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
    matched_offsets = [
        item.score_offset
        for item in condition_results
        if item.matched and not item.negated
    ]
    return CustomFormatEvaluation(
        custom_format_id=fmt.id,
        custom_format_name=fmt.name,
        matched=matched,
        conditions=condition_results,
        score=int(score),
        score_offset=max(matched_offsets) if matched and matched_offsets else 0,
        group_results=group_results,
    )


def evaluate_condition(
    condition: FormatCondition,
    release: ReleaseParseResult | str,
    *,
    context: Mapping[str, Any] | None = None,
) -> ConditionResult:
    """Evaluate one condition card for parser/test tooling."""

    item = condition
    return _evaluate_condition(item, _coerce_release_parse(release), context or {})


def _score_for(
    fmt: CustomFormat,
    profile_scores: Mapping[str, int] | None,
    overrides: Mapping[str, int] | None,
) -> int:
    profile_scores = profile_scores or {}
    overrides = overrides or {}
    if fmt.id in overrides:
        return int(overrides[fmt.id])
    if fmt.id in profile_scores:
        return int(profile_scores[fmt.id])
    return 0


def _score_applies_to_format(
    fmt: CustomFormat,
    profile_scores: Mapping[str, int] | None,
    overrides: Mapping[str, int] | None,
) -> bool:
    """Whether this format participates in the current scoring policy.

    A zero score is still a deliberate selection because rule offsets may add
    to it. Absence from both the Quality Profile and title overrides means the
    format remains explainable but must not affect the total score.
    """

    if profile_scores is None and overrides is None:
        return True
    profile_scores = profile_scores or {}
    overrides = overrides or {}
    return (
        fmt.id in profile_scores
        or fmt.id in overrides
    )


def evaluate_custom_formats(
    custom_formats: Iterable[CustomFormat],
    release: ReleaseParseResult | str,
    *,
    profile_scores: Mapping[str, int] | None = None,
    score_overrides: Mapping[str, int] | None = None,
    context: Mapping[str, Any] | None = None,
    include_disabled: bool = False,
) -> CustomFormatSetEvaluation:
    """Evaluate all enabled formats and sum only matched contributions."""

    parsed = _coerce_release_parse(release)
    evaluation_formats: list[CustomFormat] = []
    evaluations: list[CustomFormatEvaluation] = []
    for item in custom_formats:
        fmt = item
        if not fmt.enabled and not include_disabled:
            continue
        evaluation_formats.append(fmt)
        evaluations.append(
            replace(
                evaluate_custom_format(
                    fmt,
                    parsed,
                    context=context,
                    score=_score_for(fmt, profile_scores, score_overrides),
                ),
                score_applies=_score_applies_to_format(fmt, profile_scores, score_overrides),
            )
        )
    # Resolve explicit takeovers only after every raw condition result exists.
    # A suppressed format keeps its condition evidence but is no longer an
    # effective match and therefore contributes no score anywhere downstream.
    by_id = {fmt.id: fmt for fmt in evaluation_formats}
    raw_matches = {fmt.id for fmt, result in zip(evaluation_formats, evaluations) if result.matched}

    def closure(format_id: str) -> set[str]:
        found: set[str] = set()
        pending = list(by_id.get(format_id).supersedes if by_id.get(format_id) else ())
        while pending:
            target = pending.pop()
            if target == format_id or target in found:
                continue
            found.add(target)
            nested = by_id.get(target)
            if nested is not None:
                pending.extend(nested.supersedes)
        return found

    # In a Quality Profile scoring context, only selected/overridden formats
    # are allowed to suppress another match. Otherwise an unselected specific
    # format could silently erase the score of a broad format the profile did
    # select. Without a scoring context every format remains active, preserving
    # standalone matching/test behavior.
    active_sources = {
        fmt.id
        for fmt, result in zip(evaluation_formats, evaluations)
        if result.matched and result.score_applies
    }
    closures = {format_id: closure(format_id) for format_id in active_sources}
    roots = {
        format_id for format_id in active_sources
        if not any(format_id in targets for source, targets in closures.items() if source != format_id)
    }
    suppressed: dict[str, set[str]] = defaultdict(set)
    for source in roots:
        for target in closures[source] & raw_matches:
            suppressed[target].add(source)

    resolved: list[CustomFormatEvaluation] = []
    for fmt, result in zip(evaluation_formats, evaluations):
        suppressors = sorted(suppressed.get(fmt.id, ()), key=lambda item: by_id[item].name.casefold())
        if suppressors:
            resolved.append(replace(
                result,
                matched=False,
                raw_matched=True,
                suppressed_by=tuple(suppressors),
                suppressed_by_names=tuple(by_id[item].name for item in suppressors),
            ))
        else:
            resolved.append(replace(result, raw_matched=result.matched))

    total = sum(item.score_contribution for item in resolved)
    return CustomFormatSetEvaluation(parsed=parsed, formats=tuple(resolved), total_score=total)


def validate_condition(condition: FormatCondition) -> tuple[str, ...]:
    """Return backend validation errors without mutating the definition."""

    item = condition
    errors: list[str] = []
    if not item.type:
        errors.append("condition type is required")
    if item.pattern is None or item.pattern == "":
        errors.append("condition pattern is required")
    if item.pattern:
        for pattern in _as_tuple(item.pattern):
            try:
                re.compile(str(pattern), 0 if item.case_sensitive else re.IGNORECASE)
            except re.error as exc:
                errors.append(f"invalid regex: {exc}")
    return tuple(errors)


def validate_custom_format(custom_format: CustomFormat) -> tuple[str, ...]:
    fmt = custom_format
    errors: list[str] = []
    if not fmt.name.strip():
        errors.append("custom format name is required")
    for index, condition in enumerate(fmt.conditions):
        errors.extend(f"condition {index}: {error}" for error in validate_condition(condition))
    return tuple(errors)



__all__ = [
    "SCHEMA_VERSION",
    "ConditionResult",
    "CustomFormat",
    "CustomFormatEvaluation",
    "CustomFormatSetEvaluation",
    "FormatCondition",
    "evaluate_condition",
    "evaluate_custom_format",
    "evaluate_custom_formats",
    "validate_condition",
    "validate_custom_format",
]
