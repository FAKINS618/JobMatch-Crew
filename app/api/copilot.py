"""Vue 求职副驾的会话、分析回合和 SSE 事件接口。"""

import asyncio
import json
import time
from collections.abc import AsyncIterator

from fastapi import APIRouter, BackgroundTasks, HTTPException, Query
from fastapi.responses import StreamingResponse

from app.database import (
    create_analysis_task,
    create_artifact_decision,
    create_copilot_message_and_turn,
    create_copilot_session,
    create_evidence_feedback,
    get_analysis_evidence_chain,
    get_copilot_session,
    get_copilot_turn,
    get_analysis_task,
    get_resume_market_search_preference,
    get_resume_market_search_trigger_by_turn,
    get_turn_auto_market_search_context,
    create_resume_market_search_trigger,
    update_copilot_turn,
    update_analysis_task,
    update_resume_market_search_trigger,
)
from app.schemas import (
    AnalysisTurnResponse,
    ArtifactDecisionCreate,
    ArtifactDecisionResponse,
    CopilotMessageCreate,
    CopilotSessionCreate,
    CopilotSessionDetailResponse,
    CopilotSessionResponse,
    AutoMarketSearchResponse,
    MarketMatchRequest,
)
from app.schemas.agent_pipeline import (
    EvidenceChainResponse,
    EvidenceFeedback,
    EvidenceFeedbackRequest,
)
from app.config import settings
from app.services.copilot_service import run_copilot_turn
from app.services.copilot_context_service import invalidate_report_context
from app.services.analysis_task_service import run_auto_market_match_task
from app.task_queue import TaskQueueUnavailable, dispatch_task


router = APIRouter(prefix="/api/v1/copilot", tags=["Copilot"])


@router.post("/sessions", response_model=CopilotSessionResponse, status_code=201)
def create_session(payload: CopilotSessionCreate) -> CopilotSessionResponse:
    try:
        session = create_copilot_session(payload.resume_version_id, payload.target_role)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    return CopilotSessionResponse.model_validate(session)


@router.get("/sessions/{session_id}", response_model=CopilotSessionDetailResponse)
def get_session(
    session_id: int,
    message_limit: int = Query(default=100, ge=1, le=200),
    turn_limit: int = Query(default=50, ge=1, le=100),
) -> CopilotSessionDetailResponse:
    session = get_copilot_session(
        session_id, message_limit=message_limit, turn_limit=turn_limit
    )
    if session is None:
        raise HTTPException(status_code=404, detail="副驾会话不存在")
    return CopilotSessionDetailResponse.model_validate(session)


@router.post(
    "/sessions/{session_id}/messages",
    response_model=AnalysisTurnResponse,
    status_code=202,
)
def send_message(
    session_id: int,
    payload: CopilotMessageCreate,
    background_tasks: BackgroundTasks,
) -> AnalysisTurnResponse:
    created = create_copilot_message_and_turn(session_id, payload.content)
    if created is None:
        raise HTTPException(status_code=404, detail="副驾会话不存在")
    _, turn = created
    try:
        dispatch_task(
            background_tasks,
            task_type="copilot_turn",
            payload={"turn_id": int(turn["id"])},
            local_runner=run_copilot_turn,
            local_args=(int(turn["id"]),),
        )
    except TaskQueueUnavailable as exc:
        update_copilot_turn(
            int(turn["id"]),
            status="failed",
            stage="failed",
            progress=100,
            error_message="任务队列暂不可用，请稍后重试。",
        )
        raise HTTPException(status_code=503, detail="任务队列暂不可用，请稍后重试") from exc
    return AnalysisTurnResponse.model_validate(turn)


@router.get("/turns/{turn_id}", response_model=AnalysisTurnResponse)
def get_turn(turn_id: int) -> AnalysisTurnResponse:
    turn = get_copilot_turn(turn_id)
    if turn is None:
        raise HTTPException(status_code=404, detail="分析回合不存在")
    return AnalysisTurnResponse.model_validate(turn)


@router.get("/turns/{turn_id}/evidence", response_model=EvidenceChainResponse)
def get_turn_evidence(turn_id: int) -> EvidenceChainResponse:
    """Return structured requirement evidence without raw prompts or resume text."""
    if get_copilot_turn(turn_id) is None:
        raise HTTPException(status_code=404, detail="分析回合不存在")
    evidence = get_analysis_evidence_chain(turn_id)
    if evidence is None:
        raise HTTPException(status_code=404, detail="证据链不存在")
    return EvidenceChainResponse.model_validate({"turn_id": turn_id, **evidence})


@router.post(
    "/turns/{turn_id}/evidence-feedback",
    response_model=EvidenceFeedback,
    status_code=201,
)
def submit_evidence_feedback(
    turn_id: int, payload: EvidenceFeedbackRequest
) -> EvidenceFeedback:
    if get_copilot_turn(turn_id) is None:
        raise HTTPException(status_code=404, detail="分析回合不存在")
    evidence = get_analysis_evidence_chain(turn_id)
    if evidence is None or not evidence.get("items"):
        raise HTTPException(status_code=409, detail="证据链尚未生成")
    item = next(
        (
            value
            for value in evidence["items"]
            if value.get("requirement", {}).get("id") == payload.requirement_id
        ),
        None,
    )
    if item is None:
        raise HTTPException(status_code=422, detail="requirement 不存在")
    candidate_map = {
        candidate.get("id"): candidate
        for candidate in item.get("candidates", [])
        if isinstance(candidate, dict)
    }
    if any(
        evidence_id not in candidate_map
        or candidate_map[evidence_id].get("requirement_id") != payload.requirement_id
        for evidence_id in payload.evidence_ids
    ):
        raise HTTPException(status_code=422, detail="evidence_id 不属于该 requirement")
    try:
        created = create_evidence_feedback(
            turn_id=turn_id,
            analysis_run_id=int(evidence["analysis_run_id"]),
            requirement_id=payload.requirement_id,
            verdict=payload.verdict,
            corrected_status=payload.corrected_status,
            evidence_ids=payload.evidence_ids,
            note=payload.note,
        )
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    turn = get_copilot_turn(turn_id)
    if turn is not None:
        invalidate_report_context(turn.get("report_id"))
    return EvidenceFeedback.model_validate(created)


@router.post(
    "/turns/{turn_id}/market-search",
    response_model=AutoMarketSearchResponse,
    status_code=202,
)
def trigger_market_search(
    turn_id: int, background_tasks: BackgroundTasks
) -> AutoMarketSearchResponse:
    """Opt-in岗位搜索：只接受已完成且建议立即投递的分析回合。"""
    context = get_turn_auto_market_search_context(turn_id)
    if context is None:
        raise HTTPException(status_code=404, detail="分析回合不存在")

    existing = get_resume_market_search_trigger_by_turn(turn_id)
    if existing is not None:
        task = get_analysis_task(int(existing["analysis_task_id"])) or {}
        return AutoMarketSearchResponse.model_validate(
            {"trigger": existing, "task_status": task.get("status", existing["status"]), "reused": True}
        )

    if context["turn_status"] != "completed":
        raise HTTPException(status_code=409, detail="分析尚未完成，暂不能启动岗位搜索")
    if (context["fit_strategy"] or {}).get("recommendation") != "apply_now":
        raise HTTPException(status_code=409, detail="当前分析建议不是立即投递")
    resume_version_id = context.get("resume_version_id")
    if resume_version_id is None:
        raise HTTPException(status_code=422, detail="当前分析未关联简历版本")
    preference = get_resume_market_search_preference(int(resume_version_id))
    if preference is None:
        raise HTTPException(status_code=404, detail="简历版本不存在")
    if not preference["auto_search_enabled"]:
        raise HTTPException(status_code=409, detail="该简历版本未开启自动搜索")
    target_role = str(context.get("target_role") or "").strip()
    if not target_role:
        raise HTTPException(status_code=422, detail="请先为简历版本设置目标岗位方向")

    payload = MarketMatchRequest(
        resume_text=context["resume_text"],
        target_role=target_role,
        city=preference.get("city", ""),
        max_results=8,
        resume_version_id=int(resume_version_id),
    )
    task_id = create_analysis_task(task_type="market_match")
    try:
        trigger = create_resume_market_search_trigger(
            resume_version_id=int(resume_version_id),
            source_turn_id=turn_id,
            analysis_task_id=task_id,
            report_id=context.get("report_id"),
            target_role=target_role,
            city=preference.get("city", ""),
        )
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    try:
        dispatch_task(
            background_tasks,
            task_type="auto_market_match",
            payload={
                "task_id": task_id,
                "trigger_id": int(trigger["id"]),
                "request": payload.model_dump(mode="json"),
            },
            local_runner=run_auto_market_match_task,
            local_args=(task_id, payload, int(trigger["id"])),
        )
    except TaskQueueUnavailable as exc:
        update_analysis_task(
            task_id,
            status="failed",
            progress=100,
            error_message="任务队列暂不可用，请稍后重试。",
        )
        update_resume_market_search_trigger(
            int(trigger["id"]),
            status="failed",
            reason="任务队列暂不可用，请稍后重试。",
        )
        raise HTTPException(status_code=503, detail="任务队列暂不可用，请稍后重试") from exc
    return AutoMarketSearchResponse.model_validate(
        {"trigger": trigger, "task_status": "pending", "reused": False}
    )


@router.get("/turns/{turn_id}/events")
async def stream_turn_events(turn_id: int) -> StreamingResponse:
    async def event_stream() -> AsyncIterator[str]:
        previous_payload = ""
        started_at = time.monotonic()
        while time.monotonic() - started_at < max(1, settings.sse_max_seconds):
            turn = get_copilot_turn(turn_id)
            if turn is None:
                yield "event: error\ndata: {\"message\": \"分析回合不存在\"}\n\n"
                return
            payload = json.dumps(turn, ensure_ascii=False, default=str)
            if payload != previous_payload:
                yield f"event: turn\ndata: {payload}\n\n"
                previous_payload = payload
            if turn["status"] in {"completed", "failed"}:
                return
            yield ": keepalive\n\n"
            await asyncio.sleep(max(0.1, settings.sse_poll_interval_seconds))
        yield "event: timeout\ndata: {\"message\": \"分析仍在运行，请稍后查询分析回合。\"}\n\n"

    return StreamingResponse(event_stream(), media_type="text/event-stream")


@router.post(
    "/artifacts/{artifact_id}/decisions",
    response_model=ArtifactDecisionResponse,
    status_code=201,
)
def decide_artifact(
    artifact_id: int, payload: ArtifactDecisionCreate
) -> ArtifactDecisionResponse:
    decision = create_artifact_decision(artifact_id, payload.decision, payload.note)
    if decision is None:
        raise HTTPException(status_code=404, detail="分析产物不存在")
    return ArtifactDecisionResponse.model_validate(decision)
