"""JD requirement extraction with a deterministic fallback."""

import re

from pydantic import BaseModel, Field

from app.agent_pipeline.structured_runner import StructuredStageError, run_structured
from app.schemas.agent_pipeline import JDRequirement
from app.services.market_profile_service import SKILL_KEYWORDS


class JDRequirementBundle(BaseModel):
    requirements: list[JDRequirement] = Field(default_factory=list, max_length=20)


def validate_requirement_quotes(requirements: list[JDRequirement], jd_text: str) -> None:
    """Ensure every source quote is an actual substring of the submitted JD."""
    requirement_ids: set[str] = set()
    for requirement in requirements:
        if requirement.id in requirement_ids:
            raise ValueError(f"岗位要求 id 重复：{requirement.id}")
        requirement_ids.add(requirement.id)
        if requirement.source_quote not in jd_text:
            raise ValueError(f"岗位要求 {requirement.id} 的 source_quote 不在原始 JD 中")


def _category(jd_text: str, skill: str) -> str:
    index = jd_text.casefold().find(skill.casefold())
    context = jd_text[max(0, index - 60) : index + len(skill) + 60]
    if any(marker in context for marker in ("加分", "优先", "最好", "nice to have")):
        return "preferred"
    return "must"


def rule_extract_requirements(jd_text: str) -> list[JDRequirement]:
    requirements: list[JDRequirement] = []
    for skill in SKILL_KEYWORDS:
        match = re.search(re.escape(skill), jd_text, flags=re.IGNORECASE)
        if not match:
            continue
        quote_start = max(0, match.start() - 35)
        quote_end = min(len(jd_text), match.end() + 55)
        # Keep the exact source substring so downstream validation is possible.
        quote = jd_text[quote_start:quote_end].strip()
        requirement_id = f"req-{len(requirements) + 1}"
        requirements.append(
            JDRequirement(
                id=requirement_id,
                text=skill,
                skill=skill,
                category=_category(jd_text, skill),
                weight=2 if _category(jd_text, skill) == "must" else 1,
                source_quote=quote,
            )
        )
        if len(requirements) >= 20:
            break
    validate_requirement_quotes(requirements, jd_text)
    return requirements


def extract_requirements(jd_text: str, target_role: str, *, use_llm: bool) -> tuple[list[JDRequirement], bool]:
    prompt = f"""
    目标岗位：{target_role}
    从以下 JD 中抽取最多 20 条技术或项目要求。
    每条包含 id、text、skill、category、weight、source_quote。
    source_quote 必须逐字来自 JD；没有证据的要求不要补写。
    只输出 JSON 对象 {{\"requirements\": [...]}}。

    JD：
    {jd_text}
    """
    try:
        bundle = run_structured(
            prompt=prompt,
            output_model=JDRequirementBundle,
            expected_output="包含 requirements 数组的 JSON 对象",
            enabled=use_llm,
        )
        validate_requirement_quotes(bundle.requirements, jd_text)
        return bundle.requirements, False
    except (StructuredStageError, ValueError):
        return rule_extract_requirements(jd_text), True
