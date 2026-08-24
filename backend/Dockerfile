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

CMD ["python", "-m", "uvicorn", "tekijin.main:app", "--host", "0.0.0.0", "--port", "8000"]
