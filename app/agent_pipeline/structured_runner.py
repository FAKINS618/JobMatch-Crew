"""Small, schema-first wrapper around CrewAI task output."""

import json
import logging
from dataclasses import dataclass
from typing import TypeVar

from crewai import Agent, Crew, Process, Task
from pydantic import BaseModel

from app.cache import build_cache_key, get_cache
from app.config import settings
from app.llm_factory import build_llm
from app.report_parser import extract_json_block


logger = logging.getLogger(__name__)


T = TypeVar("T", bound=BaseModel)


class StructuredStageError(RuntimeError):
    """A stage could not produce a validated structured result."""


@dataclass(frozen=True)
class StageOutcome:
    """Validated stage value plus bounded failure/retry metadata."""

    value: T | None
    retry_count: int = 0
    validation_error: str = ""
    degraded: bool = False
    cache_hit: bool = False


def _error_summary(error: Exception) -> str:
    """Keep diagnostics useful without persisting model prompts or raw output."""
    message = " ".join(str(error).split())
    return f"{type(error).__name__}: {message[:160]}"


def _call_agent(prompt: str, expected_output: str) -> str:
    agent = Agent(
        role="求职证据流水线分析 Agent",
        goal="严格按照给定 JSON 契约输出，不补造输入中不存在的事实",
        backstory=(
            "你负责一个可审计的求职分析阶段。只能使用任务输入，"
            "不能输出额外 Markdown 或虚构证据。"
        ),
        llm=build_llm(),
        verbose=False,
    )
    task = Task(description=prompt, expected_output=expected_output, agent=agent)
    result = Crew(
        agents=[agent],
        tasks=[task],
        process=Process.sequential,
        verbose=False,
    ).kickoff()
    return str(result).strip()


def run_structured(
    *,
    prompt: str,
    output_model: type[T],
    expected_output: str,
    enabled: bool,
    cache_namespace: str | None = None,
    cache_identity: dict | None = None,
    cache_ttl_seconds: int | None = None,
) -> StageOutcome:
    """Run one structured stage and repair invalid JSON at most once."""
    if not enabled or not settings.deepseek_api_key:
        return StageOutcome(
            value=None,
            validation_error="StructuredStageError: LLM 未配置，使用规则降级",
            degraded=True,
        )

    cache = get_cache()
    cache_key = None
    if cache_namespace and cache_identity and settings.cache_enabled:
        cache_key = build_cache_key(cache_namespace, cache_identity)
        cached = cache.get_json(cache_key)
        if cached is not None:
            try:
                cached_value = cached.get("value", cached) if isinstance(cached, dict) else cached
                return StageOutcome(
                    value=output_model.model_validate(cached_value),
                    cache_hit=True,
                )
            except Exception as error:
                # A stale or incompatible cache entry must never block a fresh call.
                cache.delete(cache_key)
                logger.warning("Ignoring invalid structured cache entry: %s", _error_summary(error))

    try:
        raw = _call_agent(prompt, expected_output)
    except Exception as error:
        logger.exception(
            "Structured stage agent call failed output_model=%s",
            output_model.__name__,
        )
        return StageOutcome(value=None, validation_error=_error_summary(error), degraded=True)
    parsed = extract_json_block(raw)
    try:
        if not parsed:
            raise ValueError("未找到可解析的 JSON 对象")
        value = output_model.model_validate(parsed)
        if cache_key and cache_ttl_seconds:
            cache.set_json(cache_key, {"value": value.model_dump(mode="json")}, cache_ttl_seconds)
        return StageOutcome(value=value)
    except Exception as first_error:
        repair_prompt = f"""
        下面的模型输出没有通过 JSON schema 校验。
        只修复 JSON 结构，不改变事实，不添加输入中不存在的证据。

        目标 schema：{json.dumps(output_model.model_json_schema(), ensure_ascii=False)}
        原始输出：
        {raw[:12000]}

        只返回一个 JSON 对象，不要 Markdown。
        """
        try:
            repaired = _call_agent(repair_prompt, expected_output)
        except Exception as error:
            logger.exception(
                "Structured stage repair call failed output_model=%s",
                output_model.__name__,
            )
            return StageOutcome(
                value=None,
                retry_count=1,
                validation_error=(
                    f"initial validation: {_error_summary(first_error)}; "
                    f"repair call: {_error_summary(error)}"
                ),
                degraded=True,
            )
        repaired_json = extract_json_block(repaired)
        try:
            value = output_model.model_validate(repaired_json)
            if cache_key and cache_ttl_seconds:
                cache.set_json(cache_key, {"value": value.model_dump(mode="json")}, cache_ttl_seconds)
            return StageOutcome(value=value, retry_count=1)
        except Exception as second_error:
            logger.warning(
                "Structured stage repair validation failed output_model=%s error=%s",
                output_model.__name__,
                _error_summary(second_error),
            )
            return StageOutcome(
                value=None,
                retry_count=1,
                validation_error=(
                    f"initial validation: {_error_summary(first_error)}; "
                    f"repair validation: {_error_summary(second_error)}"
                ),
                degraded=True,
            )
