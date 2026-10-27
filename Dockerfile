FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

WORKDIR /app

COPY requirements.txt .
RUN pip install -r requirements.txt

COPY app ./app

# Unprivileged user. UID 1000 matches the usual host user, so a bind-mounted ./data stays writable;
# override with --build-arg APP_UID=$(id -u) if yours differs.
ARG APP_UID=1000
RUN useradd --uid "${APP_UID}" --no-create-home --shell /usr/sbin/nologin app \
    && mkdir -p /app/data/reports /app/data/config \
    && chown -R app:app /app/data
USER app

ENV HOST=0.0.0.0 \
    PORT=8000 \
    REPORTS_DIR=/app/data/reports \
    CONFIG_DIR=/app/data/config \
    TZ=Asia/Seoul

EXPOSE 8000
# Reports (data/reports) and settings page state (data/config: tokens.csv, settings.json).
VOLUME ["/app/data"]

HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
    CMD python -c "import os, urllib.request; urllib.request.urlopen('http://127.0.0.1:%s/index' % os.environ.get('PORT', '8000'), timeout=4)"

CMD ["python", "-m", "app"]
