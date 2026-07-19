"""Small, schema-first wrapper around CrewAI task output."""

import json
from typing import TypeVar

from crewai import Agent, Crew, Process, Task
from pydantic import BaseModel

from app.config import settings
from app.llm_factory import build_llm
from app.report_parser import extract_json_block


T = TypeVar("T", bound=BaseModel)


class StructuredStageError(RuntimeError):
    """A stage could not produce a validated structured result."""


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
) -> T:
    """Run one structured stage and repair invalid JSON at most once."""
    if not enabled or not settings.deepseek_api_key:
        raise StructuredStageError("LLM 未配置，使用规则降级")

    raw = _call_agent(prompt, expected_output)
    parsed = extract_json_block(raw)
    try:
        return output_model.model_validate(parsed)
    except Exception as first_error:
        repair_prompt = f"""
        下面的模型输出没有通过 JSON schema 校验。
        只修复 JSON 结构，不改变事实，不添加输入中不存在的证据。

        目标 schema：{json.dumps(output_model.model_json_schema(), ensure_ascii=False)}
        原始输出：
        {raw[:12000]}

        只返回一个 JSON 对象，不要 Markdown。
        """
        repaired = _call_agent(repair_prompt, expected_output)
        repaired_json = extract_json_block(repaired)
        try:
            return output_model.model_validate(repaired_json)
        except Exception as second_error:
            raise StructuredStageError(
                f"结构化输出校验失败：{first_error}; 修复重试失败：{second_error}"
            ) from second_error

