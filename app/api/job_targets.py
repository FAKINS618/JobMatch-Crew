"""投递目标与投递过程事件接口。"""

from fastapi import APIRouter, HTTPException, Query

from app.database import (
    create_application_event,
    get_job_target_timeline,
    create_interview_review,
    list_interview_reviews,
    update_interview_review,
    confirm_interview_actions,
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
    JobTargetTimelineResponse,
    InterviewReviewCreate,
    InterviewReviewResponse,
    InterviewReviewUpdate,
    InterviewActionConfirm,
    ActionItemResponse,
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





@router.patch("/interview-reviews/{review_id}", response_model=InterviewReviewResponse)
def patch_interview_review(
    review_id: int, payload: InterviewReviewUpdate
) -> InterviewReviewResponse:
    try:
        review = update_interview_review(review_id, payload)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    if review is None:
        raise HTTPException(status_code=404, detail="面试复盘不存在")
    return InterviewReviewResponse.model_validate(review)

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

@router.get("/{target_id}/timeline", response_model=JobTargetTimelineResponse)
def get_target_timeline(target_id: int) -> JobTargetTimelineResponse:
    timeline = get_job_target_timeline(target_id)
    if timeline is None:
        raise HTTPException(status_code=404, detail="投递目标不存在")
    return JobTargetTimelineResponse.model_validate(timeline)


@router.get("/{target_id}/interview-reviews", response_model=list[InterviewReviewResponse])
def get_target_interview_reviews(target_id: int) -> list[InterviewReviewResponse]:
    reviews = list_interview_reviews(target_id)
    if reviews is None:
        raise HTTPException(status_code=404, detail="投递目标不存在")
    return [InterviewReviewResponse.model_validate(item) for item in reviews]


@router.post("/{target_id}/interview-reviews", response_model=InterviewReviewResponse, status_code=201)
def add_interview_review(
    target_id: int, payload: InterviewReviewCreate
) -> InterviewReviewResponse:
    try:
        review = create_interview_review(target_id, payload)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    return InterviewReviewResponse.model_validate(review)




@router.post("/interview-reviews/{review_id}/actions", response_model=list[ActionItemResponse], status_code=201)
def confirm_review_actions(
    review_id: int, payload: InterviewActionConfirm
) -> list[ActionItemResponse]:
    try:
        items = confirm_interview_actions(review_id, payload.skills)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    return [ActionItemResponse.model_validate(item) for item in items]
