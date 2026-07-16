"""Vue 求职副驾的会话、分析回合和 SSE 事件接口。"""

import asyncio
import json
from collections.abc import AsyncIterator

from fastapi import APIRouter, BackgroundTasks, HTTPException
from fastapi.responses import StreamingResponse

from app.database import (
    create_artifact_decision,
    create_copilot_message_and_turn,
    create_copilot_session,
    get_copilot_session,
    get_copilot_turn,
)
from app.schemas import (
    AnalysisTurnResponse,
    ArtifactDecisionCreate,
    ArtifactDecisionResponse,
    CopilotMessageCreate,
    CopilotSessionCreate,
    CopilotSessionDetailResponse,
    CopilotSessionResponse,
)
from app.services.copilot_service import run_copilot_turn


router = APIRouter(prefix="/api/v1/copilot", tags=["Copilot"])


@router.post("/sessions", response_model=CopilotSessionResponse, status_code=201)
def create_session(payload: CopilotSessionCreate) -> CopilotSessionResponse:
    try:
        session = create_copilot_session(payload.resume_version_id, payload.target_role)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    return CopilotSessionResponse.model_validate(session)


@router.get("/sessions/{session_id}", response_model=CopilotSessionDetailResponse)
def get_session(session_id: int) -> CopilotSessionDetailResponse:
    session = get_copilot_session(session_id)
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
    background_tasks.add_task(run_copilot_turn, int(turn["id"]))
    return AnalysisTurnResponse.model_validate(turn)


@router.get("/turns/{turn_id}", response_model=AnalysisTurnResponse)
def get_turn(turn_id: int) -> AnalysisTurnResponse:
    turn = get_copilot_turn(turn_id)
    if turn is None:
        raise HTTPException(status_code=404, detail="分析回合不存在")
    return AnalysisTurnResponse.model_validate(turn)


@router.get("/turns/{turn_id}/events")
async def stream_turn_events(turn_id: int) -> StreamingResponse:
    async def event_stream() -> AsyncIterator[str]:
        previous_payload = ""
        for _ in range(60):
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
            await asyncio.sleep(0.5)

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
