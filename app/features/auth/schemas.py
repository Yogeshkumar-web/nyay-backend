from pydantic import BaseModel, EmailStr, Field, ConfigDict, field_validator

from app.features.users.schemas import LawyerProfileResponse


class RegisterRequest(BaseModel):
    email: EmailStr
    password: str = Field(..., min_length=8)
    full_name: str = Field(..., min_length=1, max_length=200)


class LoginRequest(BaseModel):
    email: EmailStr
    password: str


class TokenResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    access_token: str
    token_type: str = "bearer"
    user: LawyerProfileResponse


class TokenPair(BaseModel):
    access_token: str
    refresh_token: str
    token_type: str = "bearer"


class RefreshTokenResponse(BaseModel):
    access_token: str
    token_type: str = "bearer"


class ChangePasswordRequest(BaseModel):
    current_password: str
    new_password: str = Field(..., min_length=8)

    @field_validator("new_password")
    @classmethod
    def validate(cls, v, info):
        if info.data.get("current_password") == v:
            raise ValueError("New password must differ")
        return v


class GoogleLoginRequest(BaseModel):
    id_token: str
