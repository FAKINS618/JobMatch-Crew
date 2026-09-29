from fastapi import APIRouter, Depends, HTTPException

from app.auth import authenticate_user, create_user, get_current_user, issue_token
from app.schemas import AuthCredentials, AuthResponse, UserResponse

router = APIRouter(prefix="/api/auth", tags=["Auth"])


@router.get("/me", response_model=UserResponse)
def current_user(user: dict = Depends(get_current_user)) -> UserResponse:
    return UserResponse.model_validate(user)


@router.post("/register", response_model=AuthResponse, status_code=201)
def register(payload: AuthCredentials) -> AuthResponse:
    try:
        user = create_user(payload.email, payload.password)
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    return AuthResponse(access_token=issue_token(user), user=UserResponse.model_validate(user))


@router.post("/login", response_model=AuthResponse)
def login(payload: AuthCredentials) -> AuthResponse:
    user = authenticate_user(payload.email, payload.password)
    if user is None:
        raise HTTPException(status_code=401, detail="邮箱或密码不正确")
    return AuthResponse(access_token=issue_token(user), user=UserResponse.model_validate(user))
