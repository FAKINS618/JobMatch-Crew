from typing import Any
from config import API_BASE_URL
import requests



class ApiRequestError(RuntimeError):
    """保留 FastAPI 返回的业务错误，供 Streamlit 直接展示给用户。"""


def _format_error_detail(response: requests.Response) -> str:
    try:
        detail = response.json().get("detail")
    except ValueError:
        return response.text.strip() or response.reason

    if isinstance(detail, str):
        return detail
    if isinstance(detail, list):
        messages = [
            item.get("msg", "请求字段不符合要求")
            for item in detail
            if isinstance(item, dict)
        ]
        return "；".join(messages) or "请求字段不符合要求"
    return str(detail or response.reason)


def _request(method: str, path: str, **kwargs: Any) -> dict[str, Any]:
    """统一处理前端到 FastAPI 的请求和错误。

    前端页面不直接拼接后端细节，所有接口调用都集中在这个文件。
    """
    response = requests.request(method, f"{API_BASE_URL}{path}", **kwargs)
    if not response.ok:
        raise ApiRequestError(_format_error_detail(response))
    return response.json()


def fetch_roles() -> list[str]:
    """读取后端支持的岗位方向列表。"""
    data = _request("GET", "/api/roles", timeout=10)
    return data.get("roles", [])


def fetch_role_detail(role_name: str) -> dict[str, Any]:
    """读取某个岗位方向的技能图谱。"""
    return _request(
        "GET",
        "/api/role-detail",
        params={"role_name": role_name},
        timeout=10,
    )


def create_job_match_report(payload: dict[str, Any]) -> dict[str, Any]:
    """提交简历和 JD，生成求职匹配报告。"""
    return _request("POST", "/api/job-match", json=payload, timeout=180)


def search_jobs(payload: dict[str, Any]) -> dict[str, Any]:
    """联网搜索岗位信息。"""
    return _request("POST", "/api/jobs/search", json=payload, timeout=60)


def list_reports() -> dict[str, Any]:
    """获取历史报告列表。"""
    return _request("GET", "/api/reports", timeout=30)


def get_report_detail(report_id: int) -> dict[str, Any]:
    """根据报告 ID 获取完整历史报告。"""
    return _request("GET", f"/api/reports/{report_id}", timeout=30)

def create_market_match_task(payload: dict[str, Any]) -> dict[str, Any]:
    """创建岗位市场匹配异步任务。"""
    return _request("POST", "/api/tasks/market-match", json=payload, timeout=30)

def get_task_detail(task_id: int) -> dict[str, Any]:
    """查询异步任务状态。"""
    return _request("GET", f"/api/tasks/{task_id}", timeout=10)


def create_job_target(payload: dict[str, Any]) -> dict[str, Any]:
    """将市场报告中的 A/B/C 推荐加入个人投递管道。"""
    return _request("POST", "/api/job-targets", json=payload, timeout=30)


def list_job_targets(status: str | None = None) -> list[dict[str, Any]]:
    """查询个人投递目标。"""
    params = {"status": status} if status else None
    return _request("GET", "/api/job-targets", params=params, timeout=30)


def update_job_target(target_id: int, payload: dict[str, Any]) -> dict[str, Any]:
    """更新投递目标的状态、备注或截止时间。"""
    return _request("PATCH", f"/api/job-targets/{target_id}", json=payload, timeout=30)


def create_action_items_from_report(
    report_id: int, payload: dict[str, Any]
) -> list[dict[str, Any]]:
    """将报告中的技能缺口转成成长任务。"""
    return _request(
        "POST", f"/api/action-items/from-report/{report_id}", json=payload, timeout=30
    )


def list_action_items(status: str | None = None) -> list[dict[str, Any]]:
    """查询个人成长任务。"""
    params = {"status": status} if status else None
    return _request("GET", "/api/action-items", params=params, timeout=30)


def update_action_item(item_id: int, payload: dict[str, Any]) -> dict[str, Any]:
    """更新成长任务状态。"""
    return _request("PATCH", f"/api/action-items/{item_id}", json=payload, timeout=30)


def create_action_evidence(item_id: int, payload: dict[str, Any]) -> dict[str, Any]:
    """为成长任务提交成果链接或复盘说明。"""
    return _request(
        "POST", f"/api/action-items/{item_id}/evidence", json=payload, timeout=30
    )


def get_dashboard_summary() -> dict[str, Any]:
    """读取求职闭环总览数据。"""
    return _request("GET", "/api/dashboard/summary", timeout=30)


def parse_resume(raw_text: str) -> dict[str, Any]:
    """将简历文本解析为待人工确认的结构化档案。"""
    return _request("POST", "/api/resumes/parse", json={"raw_text": raw_text}, timeout=120)


def save_resume_version(payload: dict[str, Any]) -> dict[str, Any]:
    """保存经用户确认的简历版本。"""
    return _request("POST", "/api/resumes/versions", json=payload, timeout=30)


def list_resume_versions() -> list[dict[str, Any]]:
    """读取可用于分析的已确认简历版本。"""
    return _request("GET", "/api/resumes/versions", timeout=30)
