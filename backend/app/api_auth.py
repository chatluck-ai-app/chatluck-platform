"""Auth endpoints: register, login, me."""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Request, status
from sqlalchemy import select

from .auth import create_access_token, hash_password, verify_password
from .deps import audit, get_current_user, get_session, get_settings
from .models import User
from .schemas import (
    LoginRequest,
    RegisterRequest,
    TokenResponse,
    UserResponse,
)

router = APIRouter(prefix="/v1/auth", tags=["auth"])


def _client_ip(request: Request) -> str:
    fwd = request.headers.get("x-forwarded-for", "")
    if fwd:
        return fwd.split(",")[0].strip()
    return request.client.host if request.client else "unknown"


@router.post("/register", response_model=TokenResponse)
async def register(body: RegisterRequest, request: Request, session=Depends(get_session)):
    if not request.app.state.register_limiter.allow(f"reg:{_client_ip(request)}"):
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail="محاولات تسجيل كتير من نفس الجهاز — جرّب بعد ساعة",
        )
    existing = await session.execute(
        select(User).where(User.email == body.email.lower())
    )
    if existing.scalar_one_or_none() is not None:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT, detail="الإيميل ده مسجل قبل كده — جرب تسجيل الدخول"
        )
    user = User(
        email=body.email.lower(),
        password_hash=hash_password(body.password),
        display_name=body.display_name,
    )
    session.add(user)
    await session.flush()
    await audit(session, user.id, "auth.register")
    await session.commit()
    settings = get_settings(request)
    return TokenResponse(access_token=create_access_token(user.id, settings))


@router.post("/login", response_model=TokenResponse)
async def login(body: LoginRequest, request: Request, session=Depends(get_session)):
    result = await session.execute(select(User).where(User.email == body.email.lower()))
    user = result.scalar_one_or_none()
    if user is None or not verify_password(body.password, user.password_hash):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="الإيميل أو كلمة السر غير صحيحة",
        )
    await audit(session, user.id, "auth.login")
    await session.commit()
    settings = get_settings(request)
    return TokenResponse(access_token=create_access_token(user.id, settings))


@router.get("/me", response_model=UserResponse)
async def me(user: User = Depends(get_current_user)):
    return UserResponse(id=user.id, email=user.email, display_name=user.display_name)


@router.patch("/me", response_model=UserResponse)
async def update_me(
    body: dict,
    user: User = Depends(get_current_user),
    session=Depends(get_session),
):
    name = (body.get("display_name") or "").strip()
    if not name or len(name) > 120:
        raise HTTPException(status_code=422, detail="الاسم لازم يكون من حرف لـ 120 حرف")
    user.display_name = name
    await audit(session, user.id, "auth.update_profile")
    await session.commit()
    return UserResponse(id=user.id, email=user.email, display_name=user.display_name)


@router.post("/change-password", status_code=204)
async def change_password(
    body: dict,
    user: User = Depends(get_current_user),
    session=Depends(get_session),
):
    current = body.get("current_password") or ""
    new = body.get("new_password") or ""
    if not verify_password(current, user.password_hash):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="كلمة السر الحالية غير صحيحة",
        )
    if len(new) < 8 or len(new) > 128:
        raise HTTPException(
            status_code=422, detail="كلمة السر الجديدة لازم تكون 8 أحرف على الأقل"
        )
    user.password_hash = hash_password(new)
    await audit(session, user.id, "auth.change_password")
    await session.commit()
