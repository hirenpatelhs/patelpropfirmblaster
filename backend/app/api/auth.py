from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Request, status
from pydantic import BaseModel, EmailStr, Field
from sqlalchemy import func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.dependencies import current_user
from app.core.security import create_access_token, hash_password, verify_password
from app.database.session import get_db
from app.models.entities import User
from slowapi import Limiter
from slowapi.util import get_remote_address


router = APIRouter(prefix="/auth", tags=["Authentication"])
limiter = Limiter(key_func=get_remote_address)


class Credentials(BaseModel):
    login: str = Field(min_length=3, max_length=320)
    password: str = Field(min_length=12, max_length=200)


class BootstrapAdmin(BaseModel):
    email: EmailStr
    username: str = Field(min_length=3, max_length=80, pattern=r"^[A-Za-z0-9_.-]+$")
    password: str = Field(min_length=12, max_length=200)


@router.post("/bootstrap", status_code=status.HTTP_201_CREATED)
@limiter.limit("5/minute")
async def bootstrap_admin(request: Request, payload: BootstrapAdmin, db: AsyncSession = Depends(get_db)) -> dict[str, str]:
    if await db.scalar(select(func.count()).select_from(User)):
        raise HTTPException(status_code=409, detail="Initial administrator already exists")
    user = User(email=str(payload.email).lower(), username=payload.username, password_hash=hash_password(payload.password), role="ADMIN")
    db.add(user)
    await db.commit()
    await db.refresh(user)
    return {"access_token": create_access_token(str(user.id), user.role), "token_type": "bearer"}


@router.post("/login")
@limiter.limit("10/minute")
async def login(request: Request, payload: Credentials, db: AsyncSession = Depends(get_db)) -> dict[str, str]:
    user = await db.scalar(select(User).where(or_(User.email == payload.login.lower(), User.username == payload.login)))
    if user is None or not user.enabled or not verify_password(payload.password, user.password_hash):
        raise HTTPException(status_code=401, detail="Invalid credentials")
    return {"access_token": create_access_token(str(user.id), user.role), "token_type": "bearer"}


@router.get("/me")
async def me(user: Annotated[User, Depends(current_user)]) -> dict[str, str]:
    return {"id": str(user.id), "email": user.email, "username": user.username, "role": user.role}
