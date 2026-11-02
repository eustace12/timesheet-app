FROM python:3.12-slim@sha256:1148dbb014615300fa7e713b2f94f550c0d1c69780c0805f84f093ceee34dd0b

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    APP_MODE=production \
    DEMO_MODE=0 \
    OUTPUT_DIR=/tmp/generated \
    GUNICORN_CMD_ARGS="--bind=0.0.0.0:8080 --workers=2 --threads=2 --timeout=90 --graceful-timeout=30 --access-logfile=- --error-logfile=- --forwarded-allow-ips="

WORKDIR /app
COPY requirements.lock ./
RUN pip install --no-cache-dir --require-hashes -r requirements.lock \
    && groupadd --gid 10001 people \
    && useradd --uid 10001 --gid people --no-create-home --shell /usr/sbin/nologin people
COPY --chown=people:people . .
USER 10001:10001
EXPOSE 8080
CMD ["gunicorn", "web_app:app"]
