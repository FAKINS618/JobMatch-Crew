"""岗位搜索的统一入口。
    业务层只消费统一的岗位字段，具体搜索源
    被隔离在适配器内。
"""

from collections.abc import Iterable
from typing import Protocol, TypedDict
from urllib.parse import urlsplit, urlunsplit

from tavily import TavilyClient

from app.config import settings


class RawJobResult(TypedDict):
    """搜索层向业务层交付的最小统一岗位结构。"""

    title: str
    url: str
    content: str
    source: str


class JobSearchProvider(Protocol):
    """不同岗位来源需要遵守的适配器协议。"""

    source_name: str

    def search(self, query: str, max_results: int) -> list[RawJobResult]:
        """返回已经标准化为 RawJobResult 的岗位列表。"""


def _clean_text(value: object) -> str:
    """消除搜索摘要中常见的多余空白，避免去重和关键词统计受影响。"""
    return " ".join(str(value or "").split())


def _canonical_url(value: str) -> str:
    """移除 URL 查询参数和 fragment，用稳定地址做跨来源去重。"""
    if not value:
        return ""

    parsed = urlsplit(value.strip())
    return urlunsplit((parsed.scheme.lower(), parsed.netloc.lower(), parsed.path.rstrip("/"), "", ""))


def normalize_job_result(item: dict, source: str) -> RawJobResult:
    """将第三方搜索结果收敛为项目内部统一字段。

    不在这里推断发布日期或岗位状态，这类业务规则由 market_profile_service
    统一处理，防止每个 Provider 得出不一致的结论。
    """
    return {
        "title": _clean_text(item.get("title")),
        "url": _canonical_url(_clean_text(item.get("url"))),
        "content": _clean_text(item.get("content")),
        "source": source,
    }


def deduplicate_job_results(results: Iterable[RawJobResult]) -> list[RawJobResult]:
    """优先按规范化 URL 去重，缺少 URL 时按标题和摘要前缀去重。"""
    unique_results: list[RawJobResult] = []
    seen: set[str] = set()

    for result in results:
        title = _clean_text(result["title"])
        url = _canonical_url(result["url"])
        content = _clean_text(result["content"])
        identity = f"url:{url}" if url else f"text:{title.lower()}|{content[:120].lower()}"

        if identity in seen:
            continue

        seen.add(identity)
        unique_results.append(
            {
                "title": title,
                "url": url,
                "content": content,
                "source": result["source"],
            }
        )

    return unique_results


class TavilyJobSearchProvider:
    """Tavily 适配器，负责把 Tavily 响应转换为统一岗位结构。"""

    source_name = "tavily"

    def __init__(self, api_key: str):
        self.client = TavilyClient(api_key=api_key)

    def search(self, query: str, max_results: int) -> list[RawJobResult]:
        response = self.client.search(
            query=query,
            max_results=max_results,
            search_depth="basic",
        )
        return [
            normalize_job_result(item, self.source_name)
            for item in response.get("results", [])
        ]


def search_jobs(query: str, max_results: int = 5) -> list[RawJobResult]:
    """搜索并去重岗位。

    当前只启用 Tavily。函数作为 Facade 保持稳定，今后可以并行聚合多个
    Provider，再将全部结果交给 deduplicate_job_results 统一清洗。
    """
    if not settings.tavily_api_key:
        raise ValueError("TAVILY_API_KEY is not configured")

    provider: JobSearchProvider = TavilyJobSearchProvider(settings.tavily_api_key)
    return deduplicate_job_results(provider.search(query=query, max_results=max_results))
