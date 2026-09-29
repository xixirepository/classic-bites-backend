import os
import urllib.request
from contextlib import asynccontextmanager

import pymysql
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse

from media import Settings, create_storage, router
from upload_guard import MediaGuardMiddleware


def create_app(settings: Settings | None = None, storage=None):
    @asynccontextmanager
    async def lifespan(application: FastAPI):
        configuration = settings or Settings.from_env()
        configuration.validate()
        application.state.settings = configuration
        pool = None
        if storage is None:
            application.state.storage, pool = create_storage(configuration)
        else:
            application.state.storage = storage
        try:
            yield
        finally:
            if pool is not None:
                pool.clear()

    application = FastAPI(
        title="Classic Bites API", version="1.1.0", lifespan=lifespan,
        description="Private MinIO media CRUD. Use Authorize with MEDIA_API_KEY. Downloads return complete files; no video Range streaming or MySQL metadata integration is included.",
    )
    application.add_middleware(MediaGuardMiddleware)
    application.include_router(router)

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
        ok = all(value == "ok" for value in checks.values())
        return JSONResponse(status_code=200 if ok else 503, content={"status": "ok" if ok else "error", "checks": checks})

    return application


app = create_app()
