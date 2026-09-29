import os
import urllib.request
from contextlib import asynccontextmanager

import pymysql
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from fastapi.exceptions import RequestValidationError
from fastapi.exception_handlers import request_validation_exception_handler

from auth import AuthSettings, AuthService, AuthStore, router as auth_router
from auth_guard import AuthGuardMiddleware

from media import Settings, create_storage, router
from upload_guard import MediaGuardMiddleware


def create_app(settings: Settings | None = None, storage=None, *,
               auth_settings: AuthSettings | None = None, auth_store=None, google_verifier=None):
    @asynccontextmanager
    async def lifespan(application: FastAPI):
        configuration = settings or Settings.from_env()
        configuration.validate()
        application.state.settings = configuration
        auth_configuration = auth_settings or AuthSettings.from_env()
        auth_configuration.validate()
        application.state.auth = (AuthService(auth_configuration, auth_store or AuthStore.from_env(), google_verifier)
                                  if auth_configuration.enabled else None)
        pool = None
        if storage is None:
            application.state.storage, pool = create_storage(configuration)
        else:
            application.state.storage = storage
        try:
            yield
        finally:
            if application.state.auth is not None:
                application.state.auth.close()
            if pool is not None:
                pool.clear()

    application = FastAPI(
        title="Classic Bites API", version="1.2.0", lifespan=lifespan,
        description="Email/password and Google authentication use UserAccessToken. Media CRUD remains admin-only with MEDIA_API_KEY; user login does not grant media administration.",
    )
    application.add_middleware(MediaGuardMiddleware)
    application.add_middleware(AuthGuardMiddleware)
    application.include_router(router)
    application.include_router(auth_router)

    @application.exception_handler(RequestValidationError)
    async def validation_error(request: Request, exc: RequestValidationError):
        if request.url.path.startswith("/auth/"):
            # Pydantic inputs/context may include passwords and tokens; never reflect them.
            return JSONResponse(status_code=422, content={"detail": {
                "code": "validation_error", "message": "입력 내용을 확인해 주세요.",
                "fields": [{"field": ".".join(str(part) for part in error["loc"]
                                             if part in ("body", "email", "password", "display_name", "id_token", "refresh_token")) or "body",
                            "type": error["type"]} for error in exc.errors()],
            }})
        return await request_validation_exception_handler(request, exc)

    @application.exception_handler(pymysql.MySQLError)
    async def database_error(request: Request, exc: pymysql.MySQLError):
        # Do not expose SQL, connection details, or driver error text to clients/logs.
        return JSONResponse(status_code=503, content={"detail": {
            "code": "auth_unavailable", "message": "서비스에 연결할 수 없습니다. 잠시 후 다시 시도해 주세요.",
        }})

    @application.get("/")
    def index():
        return {"service": "classic-bites-fastapi", "docs": "/docs", "health": "/health", "ready": "/ready"}

    @application.get("/health")
    def health():
        return {"status": "ok"}

    @application.get("/ready")
    def ready(request: Request):
        checks = {}
        try:
            with pymysql.connect(
                host=os.environ["MYSQL_HOST"],
                port=int(os.environ["MYSQL_PORT"]),
                user=os.environ["MYSQL_USER"],
                password=os.environ["MYSQL_PASSWORD"],
                database=os.environ["MYSQL_DATABASE"],
                connect_timeout=2, read_timeout=2, write_timeout=2,
            ) as connection:
                with connection.cursor() as cursor:
                    cursor.execute("SELECT 1")
                    checks["mysql"] = "ok" if cursor.fetchone() == (1,) else "error"
        except Exception:
            checks["mysql"] = "error"
        try:
            url = request.app.state.settings.endpoint.rstrip("/") + "/minio/health/cluster"
            with urllib.request.urlopen(url, timeout=2) as response:
                checks["minio"] = "ok" if response.status == 200 else "error"
            checks["media_bucket"] = "ok" if request.app.state.storage.bucket_exists(request.app.state.settings.bucket) else "error"
        except Exception:
            checks["minio"] = "error"
            checks["media_bucket"] = "error"
        if request.app.state.auth is not None:
            try:
                request.app.state.auth.store.check_schema()
                checks["auth_schema"] = "ok"
            except Exception:
                checks["auth_schema"] = "error"
        ok = all(value == "ok" for value in checks.values())
        return JSONResponse(status_code=200 if ok else 503, content={"status": "ok" if ok else "error", "checks": checks})

    return application


app = create_app()
