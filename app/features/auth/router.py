from fastapi import APIRouter, Cookie, Depends, Response
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.dependencies import CurrentUser
from app.core.config import settings
from app.db.session import get_db
from app.features.auth.schemas import (
    ChangePasswordRequest,
    GoogleLoginRequest,
    LoginRequest,
    RegisterRequest,
)
from app.features.auth.service import AuthService

router = APIRouter(prefix="/auth", tags=["Auth"])

_ACCESS_MAX_AGE = settings.ACCESS_TOKEN_EXPIRE_MINUTES * 60
_REFRESH_MAX_AGE = settings.REFRESH_TOKEN_EXPIRE_DAYS * 24 * 3600


def _cookie_options() -> dict:
    opts = {
        "httponly": True,
        "secure": settings.COOKIE_SECURE,
        "samesite": settings.COOKIE_SAMESITE,
        "path": "/",
    }
    if settings.COOKIE_DOMAIN:
        opts["domain"] = settings.COOKIE_DOMAIN
    return opts


def _set_auth_cookies(response: Response, access: str, refresh: str):
    cookie_opts = _cookie_options()
    response.set_cookie("access_token", access, max_age=_ACCESS_MAX_AGE, **cookie_opts)
    response.set_cookie(
        "refresh_token", refresh, max_age=_REFRESH_MAX_AGE, **cookie_opts
    )


def _clear_auth_cookies(response: Response):
    delete_opts = {"path": "/"}
    if settings.COOKIE_DOMAIN:
        delete_opts["domain"] = settings.COOKIE_DOMAIN
    response.delete_cookie("access_token", **delete_opts)
    response.delete_cookie("refresh_token", **delete_opts)


@router.post("/register", status_code=201)
async def register(
    body: RegisterRequest, response: Response, db: AsyncSession = Depends(get_db)
):
    svc = AuthService(db)
    tokens = await svc.register(body.email, body.password, body.full_name)
    _set_auth_cookies(response, tokens["access_token"], tokens["refresh_token"])
    return {"success": True, "data": tokens["public_data"]}


@router.post("/login")
async def login(
    body: LoginRequest, response: Response, db: AsyncSession = Depends(get_db)
):
    svc = AuthService(db)
    result = await svc.login(body.email, body.password)
    _set_auth_cookies(response, result["access_token"], result["refresh_token"])
    return {"success": True, "data": result["public_data"]}


@router.post("/login/google")
async def login_google(
    body: GoogleLoginRequest, response: Response, db: AsyncSession = Depends(get_db)
):
    svc = AuthService(db)
    result = await svc.login_with_google(body.id_token)
    _set_auth_cookies(response, result["access_token"], result["refresh_token"])
    return {"success": True, "data": result["public_data"]}


@router.post("/google")
async def google(
    body: GoogleLoginRequest, response: Response, db: AsyncSession = Depends(get_db)
):
    return await login_google(body, response, db)


@router.post("/refresh")
async def refresh(
    response: Response,
    refresh_token: str | None = Cookie(None),
    db: AsyncSession = Depends(get_db),
):
    svc = AuthService(db)
    result = await svc.refresh(refresh_token)
    _set_auth_cookies(response, result["access_token"], result["refresh_token"])
    return {
        "success": True,
        "data": {"access_token": result["access_token"], "token_type": "bearer"},
    }


@router.post("/change-password")
async def change_password(
    body: ChangePasswordRequest, user: CurrentUser, db: AsyncSession = Depends(get_db)
):
    svc = AuthService(db)
    await svc.change_password(user.id, body.current_password, body.new_password)
    return {"success": True, "data": {}}


@router.post("/logout")
async def logout(response: Response):
    _clear_auth_cookies(response)
    return {"success": True, "data": {}}
