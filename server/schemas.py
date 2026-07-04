"""Schémas Pydantic de l'API v2 (auth & comptes — les schémas métier
arriveront avec les chantiers suivants)."""
from datetime import datetime

from pydantic import BaseModel, ConfigDict, EmailStr, Field


class RegisterIn(BaseModel):
    email: EmailStr
    password: str = Field(min_length=8, max_length=200)
    display_name: str = Field(min_length=1, max_length=120)


class LoginIn(BaseModel):
    email: EmailStr
    password: str


class ChangePasswordIn(BaseModel):
    current_password: str
    new_password: str = Field(min_length=8, max_length=200)


class SetPasswordIn(BaseModel):
    new_password: str = Field(min_length=8, max_length=200)


class UserOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    email: EmailStr
    display_name: str
    role: str
    status: str
    quota_short_tokens: int
    quota_short_hours: int
    quota_long_tokens: int
    quota_long_days: int
    created_at: datetime


class TokenOut(BaseModel):
    token: str
    user: UserOut


class UserCreateIn(BaseModel):
    """Création directe par l'admin (compte actif immédiatement)."""
    email: EmailStr
    password: str = Field(min_length=8, max_length=200)
    display_name: str = Field(min_length=1, max_length=120)
    role: str = Field(default="user", pattern="^(admin|user)$")


class UserPatchIn(BaseModel):
    display_name: str | None = Field(default=None, min_length=1, max_length=120)
    role: str | None = Field(default=None, pattern="^(admin|user)$")
    quota_short_tokens: int | None = Field(default=None, ge=0)
    quota_short_hours: int | None = Field(default=None, ge=0)
    quota_long_tokens: int | None = Field(default=None, ge=0)
    quota_long_days: int | None = Field(default=None, ge=0)
