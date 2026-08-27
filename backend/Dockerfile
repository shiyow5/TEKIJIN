# TEKIJIN backend image — FastAPI + the C1-C8 agent, real dense embeddings.
#
# Includes requirements-ml.txt (sentence-transformers/torch) so the running
# container can embed queries at request time (C3), matching local dev with
# `make setup-ml`. The image does NOT bundle the embedding model weights —
# mount a Hugging Face cache volume (see docker-compose.yml) to avoid a
# multi-GB download on first request.
FROM python:3.12-slim

WORKDIR /app

RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt requirements-ml.txt ./
RUN pip install --no-cache-dir -r requirements.txt -r requirements-ml.txt

COPY src ./src

ENV PYTHONPATH=/app/src \
    PYTHONUNBUFFERED=1

EXPOSE 8000

# `--workers 1` is explicit, not a default: without it uvicorn falls back to
# $WEB_CONCURRENCY, and Compose passes the repo-root .env into this container
# wholesale — so a stray WEB_CONCURRENCY would fork the API. The session
# dispatch registry is in-process (#76), so a second worker loses sessions.
# `backend/tests/test_single_worker_pin.py` holds this for every launch path.
CMD ["python", "-m", "uvicorn", "tekijin.main:app", "--host", "0.0.0.0", "--port", "8000", "--workers", "1"]
