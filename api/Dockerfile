FROM python:3.13.15-slim-bookworm
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 PIP_DISABLE_PIP_VERSION_CHECK=1
WORKDIR /app
COPY requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt && useradd --uid 10001 --create-home app
COPY main.py media.py upload_guard.py probe.py auth.py auth_store.py auth_guard.py migrate_auth.py prune_auth.py ./
COPY migrations/ ./migrations/
COPY catalog.py catalog_store.py catalog_learning.py catalog_media.py catalog_guard.py migrate_catalog.py ./
USER 10001:10001
EXPOSE 8000
CMD ["python", "-m", "uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8000", "--no-proxy-headers"]
