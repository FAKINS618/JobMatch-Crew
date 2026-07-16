"""统一创建 CrewAI 使用的 LLM 客户端。

市场分析、简历解析和多 Agent 流程可以共享同一个稳定入口。
"""

from crewai import LLM

from app.config import settings


def build_llm(*, temperature: float | None = None) -> LLM:
    """按照统一配置创建 LLM，调用方可为确定性解析覆盖温度。"""
    return LLM(
        model=settings.model,
        api_key=settings.deepseek_api_key,
        base_url=settings.deepseek_base_url,
        temperature=settings.temperature if temperature is None else temperature,
        max_tokens=settings.max_tokens,
        timeout=120,
    )
