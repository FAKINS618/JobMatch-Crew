"""岗位详情页的轻量验证。

只验证搜索结果已经返回的 URL，并优先读取公开网页中的 JobPosting JSON-LD。
验证失败保持 unknown，不把“无法访问”误判成“岗位已失效”。
"""

import json
import re
from datetime import date
from html import unescape
from typing import Any

import requests

from app.schemas import JobPost
from app.services.market_profile_service import (
    EXPIRED_KEYWORDS,
    calc_freshness_score,
    detect_job_status,
)


REQUEST_HEADERS = {
    "User-Agent": "CS-JobMate/0.1 (job-market-verification; contact: local-demo)"
}
APPLY_KEYWORDS = ("立即投递", "投递简历", "申请职位", "apply now", "apply")


def verify_job_post(post: JobPost, timeout: float = 8) -> JobPost:
    """补充岗位详情信息，并返回新的 JobPost 副本。

    HTTP 错误、页面反爬或无结构化字段都只会降低验证状态，不会中断整次市场
    分析任务。调用方应只对已通过相关性过滤的少量岗位调用此函数。
    """
    if not post.url or post.status == "expired":
        return post.model_copy(update={"verification_status": "skipped"})

    try:
        response = requests.get(
            post.url,
            headers=REQUEST_HEADERS,
            timeout=timeout,
        )
        response.raise_for_status()
    except requests.RequestException as exc:
        return post.model_copy(
            update={
                "verification_status": "unavailable",
                "verification_reason": f"详情页验证失败：{type(exc).__name__}",
            }
        )

    page_text = _to_plain_text(response.text)
    if any(keyword.casefold() in page_text.casefold() for keyword in EXPIRED_KEYWORDS):
        return post.model_copy(
            update={
                "status": "expired",
                "freshness_score": 0,
                "invalid_reason": "详情页显示岗位已失效",
                "verification_status": "verified",
                "verification_reason": "命中详情页失效标记",
            }
        )

    job_posting = _extract_job_posting(response.text)
    if job_posting:
        updated_post = _apply_job_posting(post, job_posting)
        status, reason = detect_job_status(updated_post)
        if status == "unknown" and _contains_apply_signal(page_text):
            status, reason = "likely_active", "详情页存在投递入口，但缺少可验证发布时间"

        return updated_post.model_copy(
            update={
                "status": status,
                "invalid_reason": reason,
                "freshness_score": calc_freshness_score(updated_post),
                "verification_status": "verified" if status == "active" else "likely",
                "verification_reason": "已解析 JobPosting JSON-LD",
            }
        )

    if _contains_apply_signal(page_text):
        return post.model_copy(
            update={
                "status": "likely_active",
                "verification_status": "likely",
                "verification_reason": "详情页存在投递入口，但未发现 JobPosting 日期字段",
                "invalid_reason": "需用户打开原链接确认发布时间",
            }
        )

    return post.model_copy(
        update={
            "verification_status": "unavailable",
            "verification_reason": "详情页未发现 JobPosting 或投递入口",
        }
    )


def _extract_job_posting(html: str) -> dict[str, Any] | None:
    """从 script[type=application/ld+json] 中提取第一个 JobPosting 对象。"""
    pattern = re.compile(
        r"<script[^>]+type=[\"']application/ld\+json[\"'][^>]*>(.*?)</script>",
        flags=re.IGNORECASE | re.DOTALL,
    )
    for match in pattern.finditer(html):
        try:
            payload = json.loads(unescape(match.group(1).strip()))
        except json.JSONDecodeError:
            continue

        for item in _walk_json(payload):
            raw_type = item.get("@type")
            types = raw_type if isinstance(raw_type, list) else [raw_type]
            if any(str(value).casefold() == "jobposting" for value in types):
                return item
    return None


def _walk_json(value: Any):
    if isinstance(value, dict):
        yield value
        for child in value.values():
            yield from _walk_json(child)
    elif isinstance(value, list):
        for child in value:
            yield from _walk_json(child)


def _apply_job_posting(post: JobPost, data: dict[str, Any]) -> JobPost:
    organization = data.get("hiringOrganization") or {}
    company = organization.get("name") if isinstance(organization, dict) else ""
    published_at = _parse_iso_date(data.get("datePosted")) or post.published_at
    deadline_at = _parse_iso_date(data.get("validThrough")) or post.deadline_at
    description = _to_plain_text(str(data.get("description") or ""))

    return post.model_copy(
        update={
            "title": str(data.get("title") or post.title),
            "company": str(company or post.company),
            "content": description or post.content,
            "published_at": published_at,
            "deadline_at": deadline_at,
        }
    )


def _parse_iso_date(value: Any) -> date | None:
    if not value:
        return None
    try:
        return date.fromisoformat(str(value)[:10])
    except ValueError:
        return None


def _to_plain_text(value: str) -> str:
    return " ".join(re.sub(r"<[^>]+>", " ", unescape(value)).split())


def _contains_apply_signal(page_text: str) -> bool:
    lower_text = page_text.casefold()
    return any(keyword.casefold() in lower_text for keyword in APPLY_KEYWORDS)
