"""Deterministic skill matching with token boundaries and negation protection."""

import re


_NEGATION_PHRASES = (
    "未使用",
    "没有使用",
    "没有直接使用",
    "没有实际使用",
    "未直接使用",
    "未实际使用",
    "未接触",
    "不熟悉",
    "不具备",
    "无经验",
    "尚未",
    "不会",
    "no experience with",
    "not used",
    "without",
    "never used",
)
_ASCII_TOKEN = r"A-Za-z0-9"


def _skill_pattern(skill: str) -> re.Pattern[str]:
    normalized = " ".join(skill.casefold().split())
    if not normalized:
        return re.compile(r"(?!x)x")
    escaped = re.escape(normalized).replace(r"\ ", r"\s+")
    # Punctuation in names such as C++, C#, REST/HTTP is part of the token.
    # Only ASCII letters/digits are blocked at either edge, preventing Java in
    # JavaScript without rejecting punctuation-delimited technology names.
    return re.compile(
        rf"(?<![{_ASCII_TOKEN}]){escaped}(?![{_ASCII_TOKEN}])", re.IGNORECASE
    )


def find_skill_spans(text: str, skill: str) -> list[tuple[int, int]]:
    """Return all token-boundary matches for a skill."""
    return [match.span() for match in _skill_pattern(skill).finditer(text or "")]


def contains_skill(text: str, skill: str) -> bool:
    return bool(find_skill_spans(text, skill))


def _is_negated_occurrence(text: str, start: int, end: int) -> bool:
    window = 28
    before = text[max(0, start - window) : start].casefold()
    after = text[end : min(len(text), end + window)].casefold()
    allowed = r"[\s:：,，、()（）/A-Za-z0-9+#.\-\u4e00-\u9fff]{0,14}"
    before_pattern = rf"(?:{'|'.join(map(re.escape, _NEGATION_PHRASES))}){allowed}$"
    after_pattern = rf"^{allowed}(?:{'|'.join(map(re.escape, _NEGATION_PHRASES))})"
    return bool(
        re.search(before_pattern, before, re.IGNORECASE)
        or re.search(after_pattern, after, re.IGNORECASE)
    )


def has_negated_skill_evidence(text: str, skill: str) -> bool:
    """Whether at least one occurrence is explicitly negated nearby."""
    return any(
        _is_negated_occurrence(text, start, end)
        for start, end in find_skill_spans(text, skill)
    )


def has_positive_skill_evidence(text: str, skill: str) -> bool:
    """Whether an occurrence remains positive after negation filtering."""
    spans = find_skill_spans(text, skill)
    return any(not _is_negated_occurrence(text, start, end) for start, end in spans)


def has_only_negated_skill_evidence(text: str, skill: str) -> bool:
    spans = find_skill_spans(text, skill)
    return bool(spans) and all(_is_negated_occurrence(text, start, end) for start, end in spans)
