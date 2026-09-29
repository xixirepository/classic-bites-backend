"""User authentication. Only server-issued opaque tokens authorize our APIs."""
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime, timezone
import hashlib
import hmac
import os
import secrets
import threading
import time
from typing import Literal

from argon2 import PasswordHasher
from argon2.exceptions import VerificationError, InvalidHashError
from cachecontrol import CacheControl
from email_validator import EmailNotValidError, validate_email
from fastapi import APIRouter, Depends, HTTPException, Request, Response
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from google.auth.exceptions import GoogleAuthError, TransportError
from google.auth.transport.requests import Request as GoogleRequest
from google.oauth2 import id_token
from pydantic import BaseModel, ConfigDict, Field, field_validator
import requests

from auth_store import AuthConflict, AuthStore, InvalidSession


def failure(status, code, message, headers=None):
    return HTTPException(status, detail={"code": code, "message": message}, headers=headers)


def normalize_email(value):
    try:
        return validate_email(value.strip(), check_deliverability=False, allow_smtputf8=False).normalized.lower()
    except EmailNotValidError:
        raise ValueError("Invalid email address") from None


@dataclass(frozen=True)
class AuthSettings:
    enabled: bool = False
    google_client_ids: tuple[str, ...] = ()
    access_ttl: int = 900
    refresh_ttl: int = 2592000
    rate_limit_salt: str = ""

    @classmethod
    def from_env(cls):
        enabled = os.environ.get("AUTH_ENABLED", "false").lower()
        if enabled not in ("true", "false"):
            raise ValueError("AUTH_ENABLED must be true or false")
        return cls(
            enabled=enabled == "true",
            google_client_ids=tuple(x.strip() for x in os.environ.get("GOOGLE_CLIENT_IDS", "").split(",") if x.strip()),
            access_ttl=int(os.environ.get("AUTH_ACCESS_TTL_SECONDS", "900")),
            refresh_ttl=int(os.environ.get("AUTH_REFRESH_TTL_SECONDS", "2592000")),
            rate_limit_salt=os.environ.get("AUTH_RATE_LIMIT_SALT", ""),
        )

    def validate(self):
        if not 60 <= self.access_ttl <= 3600 or not self.access_ttl < self.refresh_ttl <= 7776000:
            raise ValueError("Invalid auth token lifetime configuration")
        if self.enabled and (len(self.rate_limit_salt) < 32 or self.rate_limit_salt == "GENERATE_ON_INSTALL"):
            raise ValueError("Set a randomly generated AUTH_RATE_LIMIT_SALT before enabling auth")
        if any(not value.endswith(".apps.googleusercontent.com") for value in self.google_client_ids):
            raise ValueError("GOOGLE_CLIENT_IDS must contain Google OAuth client IDs")


class Input(BaseModel):
    model_config = ConfigDict(extra="forbid")


class LoginInput(Input):
    email: str = Field(min_length=3, max_length=254)
    password: str = Field(min_length=1, max_length=128)
    _email = field_validator("email")(normalize_email)


class SignupInput(LoginInput):
    password: str = Field(min_length=12, max_length=128)
    display_name: str = Field(min_length=1, max_length=80)

    @field_validator("display_name", mode="before")
    @classmethod
    def clean_name(cls, value):
        if not isinstance(value, str):
            return value
        value = value.strip()
        if not value or any(ord(c) < 32 or ord(c) == 127 for c in value):
            raise ValueError("Invalid display name")
        return value


class GoogleInput(Input):
    id_token: str = Field(min_length=1, max_length=8192)


class RefreshInput(Input):
    refresh_token: str = Field(min_length=43, max_length=43, pattern=r"^[A-Za-z0-9_-]+$")


class UserOutput(BaseModel):
    id: str
    email: str
    display_name: str
    provider: Literal["password", "google"]
    email_verified: bool
    created_at: datetime


class TokenOutput(BaseModel):
    access_token: str
    refresh_token: str
    token_type: Literal["bearer"] = "bearer"
    expires_in: int
    refresh_expires_in: int
    user: UserOutput
    is_new_user: bool = False


def public_user(user):
    return UserOutput(
        id=user["id"], email=user["email"], display_name=user["display_name"],
        provider="google" if user["google_sub"] else "password",
        email_verified=bool(user["email_verified"]),
        created_at=datetime.fromtimestamp(user["created_at"], timezone.utc),
    )


def digest(token):
    return hashlib.sha256(token.encode()).hexdigest()


class BoundedGoogleRequest(GoogleRequest):
    def __call__(self, *args, **kwargs):
        kwargs["timeout"] = 5
        return super().__call__(*args, **kwargs)


class GoogleVerifier:
    def __init__(self, audiences):
        self.audiences = audiences
        self.session = CacheControl(requests.Session())
        self.request = BoundedGoogleRequest(session=self.session)
        self.lock = threading.Lock()

    def close(self):
        self.session.close()

    def verify(self, token):
        if not self.audiences:
            raise failure(503, "google_not_configured", "Google 로그인이 아직 설정되지 않았습니다.")
        try:
            # Session/cache is shared per app; serialize its use across worker threads.
            with self.lock:
                claims = id_token.verify_oauth2_token(token, self.request, audience=list(self.audiences))
            if claims.get("aud") not in self.audiences or claims.get("iss") not in ("accounts.google.com", "https://accounts.google.com"):
                raise ValueError("Invalid claims")
            sub = claims.get("sub")
            if not isinstance(sub, str) or not sub or len(sub) > 255 or not sub.isascii():
                raise ValueError("Invalid subject")
            if claims.get("email_verified") is not True:
                raise ValueError("Email is not verified")
            email = normalize_email(claims.get("email", ""))
            name = claims.get("name")
            if not isinstance(name, str) or not name.strip():
                name = email.split("@")[0]
            name = "".join(c for c in name if ord(c) >= 32 and ord(c) != 127).strip()[:80] or "Google 사용자"
            return {"sub": sub, "email": email, "name": name,
                    "email_verified": email.endswith("@gmail.com") or bool(claims.get("hd"))}
        except (TransportError, requests.RequestException):
            raise failure(503, "google_unavailable", "Google 인증 서버에 연결할 수 없습니다. 잠시 후 다시 시도해 주세요.") from None
        except (ValueError, TypeError, AttributeError, KeyError, GoogleAuthError):
            raise failure(401, "invalid_google_token", "Google 인증 정보가 유효하지 않습니다.") from None


class AuthService:
    def __init__(self, settings, store, verifier=None, clock=time.time):
        self.settings, self.store, self.clock = settings, store, clock
        self.verifier = verifier or GoogleVerifier(settings.google_client_ids)
        # 2 * 19 MiB bounds memory consumed by concurrent password verification.
        self.hasher = PasswordHasher(time_cost=2, memory_cost=19456, parallelism=1)
        self.dummy_hash = self.hasher.hash(secrets.token_urlsafe(32))
        self.expensive_work = threading.BoundedSemaphore(2)

    def close(self):
        if hasattr(self.verifier, "close"):
            self.verifier.close()

    def now(self):
        return int(self.clock())

    @contextmanager
    def capacity(self):
        if not self.expensive_work.acquire(blocking=False):
            raise failure(429, "rate_limited", "요청이 많습니다. 잠시 후 다시 시도해 주세요.", {"Retry-After": "2"})
        try:
            yield
        finally:
            self.expensive_work.release()

    def limit(self, scope, value, count, window):
        key = hmac.new(self.settings.rate_limit_salt.encode(), (scope + ":" + value).encode(), hashlib.sha256).hexdigest()
        retry = self.store.hit_rate_limit(key, count, window, self.now())
        if retry:
            raise failure(429, "rate_limited", "요청이 많습니다. 잠시 후 다시 시도해 주세요.", {"Retry-After": str(retry)})

    def tokens(self, user, new=False):
        access, refresh, now = secrets.token_urlsafe(32), secrets.token_urlsafe(32), self.now()
        result = self.store.create_session(user["id"], digest(access), digest(refresh), now,
                                           self.settings.access_ttl, self.settings.refresh_ttl)
        return self.token_output(user, access, refresh, result["refresh_expires_at"], now, new)

    def token_output(self, user, access, refresh, refresh_expiry, now, new=False):
        return TokenOutput(access_token=access, refresh_token=refresh,
                           expires_in=min(self.settings.access_ttl, refresh_expiry - now),
                           refresh_expires_in=refresh_expiry - now, user=public_user(user), is_new_user=new)

    def signup(self, data):
        self.limit("signup-email", data.email, 5, 3600)
        with self.capacity():
            password_hash = self.hasher.hash(data.password)
        try:
            user = self.store.create_user(data.email, data.display_name, password_hash=password_hash, now=self.now())
        except AuthConflict:
            raise failure(409, "email_in_use", "이미 사용 중인 이메일입니다. 기존 가입 방법으로 로그인해 주세요.") from None
        return self.tokens(user, True)

    def login(self, data):
        self.limit("login-email", data.email, 10, 300)
        user = self.store.find_user_by_email(data.email)
        password_hash = user["password_hash"] if user and user["password_hash"] else self.dummy_hash
        with self.capacity():
            try:
                valid = self.hasher.verify(password_hash, data.password)
            except (VerificationError, InvalidHashError):
                valid = False
        if not valid or not user or not user["password_hash"]:
            raise failure(401, "invalid_credentials", "이메일 또는 비밀번호를 확인해 주세요.")
        return self.tokens(user)

    def google(self, data):
        with self.capacity():
            identity = self.verifier.verify(data.id_token)
        user = self.store.find_user_by_google_sub(identity["sub"])
        new = False
        if user is None:
            try:
                user = self.store.create_user(identity["email"], identity["name"], google_sub=identity["sub"],
                                              email_verified=identity["email_verified"], now=self.now())
                new = True
            except AuthConflict:
                # A concurrent Google signup may have inserted this exact subject.
                user = self.store.find_user_by_google_sub(identity["sub"])
                if user is None:
                    raise failure(409, "email_in_use", "이미 사용 중인 이메일입니다. 기존 가입 방법으로 로그인해 주세요.") from None
        return self.tokens(user, new)

    def refresh(self, token):
        access, refresh, now = secrets.token_urlsafe(32), secrets.token_urlsafe(32), self.now()
        try:
            result = self.store.rotate_session(digest(token), digest(access), digest(refresh), now, self.settings.access_ttl)
        except InvalidSession:
            raise invalid_token() from None
        return self.token_output(result["user"], access, refresh, result["refresh_expires_at"], now)


def invalid_token():
    return failure(401, "invalid_token", "로그인이 만료되었습니다. 다시 로그인해 주세요.", {"WWW-Authenticate": "Bearer"})


def service(request: Request):
    auth = request.app.state.auth
    if auth is None:
        raise failure(503, "auth_disabled", "로그인 서비스가 아직 활성화되지 않았습니다.")
    return auth


def write_service(request: Request, auth=Depends(service)):
    peer = request.client.host if request.client else "unknown"
    auth.limit("auth-ip", peer, 60, 300)
    return auth


bearer = HTTPBearer(auto_error=False, scheme_name="UserAccessToken")


def current_user(auth=Depends(service), credentials: HTTPAuthorizationCredentials | None = Depends(bearer)):
    if credentials is None or len(credentials.credentials) != 43:
        raise invalid_token()
    try:
        return public_user(auth.store.get_user_for_access(digest(credentials.credentials), auth.now()))
    except InvalidSession:
        raise invalid_token() from None


router = APIRouter(prefix="/auth", tags=["Authentication"])


@router.post("/signup", response_model=TokenOutput, status_code=201)
def signup(data: SignupInput, auth=Depends(write_service)):
    return auth.signup(data)


@router.post("/login", response_model=TokenOutput)
def login(data: LoginInput, auth=Depends(write_service)):
    return auth.login(data)


@router.post("/google", response_model=TokenOutput)
def google(data: GoogleInput, auth=Depends(write_service)):
    return auth.google(data)


@router.post("/refresh", response_model=TokenOutput)
def refresh(data: RefreshInput, auth=Depends(write_service)):
    return auth.refresh(data.refresh_token)


@router.post("/logout", status_code=204)
def logout(data: RefreshInput, auth=Depends(write_service)):
    auth.store.revoke_session(digest(data.refresh_token))
    return Response(status_code=204)


@router.get("/me", response_model=UserOutput)
def me(user=Depends(current_user)):
    return user
