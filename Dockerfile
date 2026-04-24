FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    HOST=0.0.0.0 \
    PORT=8000 \
    EDUPLUS_ENABLE_LOCAL_OUTPUT=false \
    EDUPLUS_AUTO_DELETE_PUBLIC_DOWNLOADS=true \
    EDUPLUS_PUBLIC_JOB_TTL_SECONDS=1800 \
    EDUPLUS_CLEANUP_INTERVAL_SECONDS=60 \
    EDUPLUS_PUBLIC_OUTPUT_ROOT=downloads/web-jobs \
    EDUPLUS_BUNDLE_ROOT=downloads/web-bundles \
    EDUPLUS_LOCAL_OUTPUT_ROOT=downloads

WORKDIR /app

COPY requirements.txt ./
RUN python -m pip install --no-cache-dir -r requirements.txt

COPY . .

RUN useradd --create-home --shell /bin/bash appuser && \
    mkdir -p /app/downloads && \
    chown -R appuser:appuser /app

USER appuser

EXPOSE 8000

CMD ["python", "-m", "eduplus_tools.web", "--host", "0.0.0.0"]
