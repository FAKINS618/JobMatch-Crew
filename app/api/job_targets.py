"""投递目标与投递过程事件接口。"""

from fastapi import APIRouter, HTTPException, Query

from app.database import (
    create_application_event,
    create_job_target,
    list_job_targets,
    update_job_target,
)
from app.schemas import (
    ApplicationEventCreate,
    ApplicationEventResponse,
    JobTargetCreate,
    JobTargetResponse,
    JobTargetUpdate,
)


router = APIRouter(prefix="/api/job-targets", tags=["Job Targets"])


@router.post("", response_model=JobTargetResponse, status_code=201)
def create_target(payload: JobTargetCreate) -> JobTargetResponse:
    try:
        return JobTargetResponse.model_validate(create_job_target(payload))
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


@router.get("", response_model=list[JobTargetResponse])
def get_targets(status: str | None = Query(default=None)) -> list[JobTargetResponse]:
    return [JobTargetResponse.model_validate(item) for item in list_job_targets(status)]


@router.patch("/{target_id}", response_model=JobTargetResponse)
def patch_target(target_id: int, payload: JobTargetUpdate) -> JobTargetResponse:
    try:
        target = update_job_target(target_id, payload)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    if target is None:
        raise HTTPException(status_code=404, detail="投递目标不存在")
    return JobTargetResponse.model_validate(target)


@router.post("/{target_id}/events", response_model=ApplicationEventResponse, status_code=201)
def add_target_event(
    target_id: int, payload: ApplicationEventCreate
) -> ApplicationEventResponse:
    event = create_application_event(target_id, payload)
    if event is None:
        raise HTTPException(status_code=404, detail="投递目标不存在")
    return ApplicationEventResponse.model_validate(event)
