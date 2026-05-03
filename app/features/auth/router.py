from fastapi import APIRouter, Cookie, Depends, HTTPException, Response, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.session import get_db
from app.features.auth.schemas import LoginRequest, RegisterRequest, TokenResponse
from app.features.auth.service import AuthService

router = APIRouter(prefix="/auth", tags=["Auth"])

_COOKIE_OPTS = dict(httponly=True, secure=False, samesite="lax")


def _set_tokens(response: Response, access_token: str, refresh_token: str) -> None:
    response.set_cookie("access_token", access_token, max_age=1800, **_COOKIE_OPTS)
    response.set_cookie("refresh_token", refresh_token, max_age=604800, **_COOKIE_OPTS)


@router.post(
    "/register",
    response_model=TokenResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Register new user",
    responses={409: {"description": "Email already registered"}},
)
async def register(
    body: RegisterRequest,
    response: Response,
    db: AsyncSession = Depends(get_db),
) -> TokenResponse:
    svc = AuthService(db)
    tokens = await svc.register(body.email, body.password, body.full_name)
    _set_tokens(response, tokens.access_token, tokens.refresh_token)
    return TokenResponse(access_token=tokens.access_token)


@router.post(
    "/login",
    summary="Login with email/password",
    responses={401: {"description": "Invalid credentials"}},
)
async def login(
    body: LoginRequest,
    response: Response,
    db: AsyncSession = Depends(get_db),
):
    svc = AuthService(db)
    result = await svc.login(body.email, body.password)
    _set_tokens(response, result["access_token"], result["refresh_token"])
    return {
        "success": True,
        "data": {
            "access_token": result["access_token"],
            "refresh_token": result["refresh_token"],
            "user": result["user"],
        },
    }


@router.post("/refresh", summary="Refresh access token via httpOnly cookie")
async def refresh(
    response: Response,
    refresh_token: str | None = Cookie(default=None),
    db: AsyncSession = Depends(get_db),
):
    if not refresh_token:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED, detail="Refresh token missing"
        )
    svc = AuthService(db)
    result = await svc.refresh(refresh_token)
    response.set_cookie(
        "access_token", result["access_token"], max_age=1800, **_COOKIE_OPTS
    )
    return {"success": True, "data": {"access_token": result["access_token"]}}


@router.post("/logout", summary="Logout — clears auth cookies")
async def logout(response: Response) -> dict:
    response.delete_cookie("access_token")
    response.delete_cookie("refresh_token")
    return {"detail": "Logged out"}
