FROM node:22-slim AS frontend-build
WORKDIR /app/frontend
COPY frontend/package*.json ./
RUN npm ci
COPY frontend/ ./
RUN npm run build

FROM python:3.11-slim
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PORT=8080 \
    HF_HOME=/opt/huggingface
WORKDIR /app
RUN apt-get update \
    && apt-get install -y --no-install-recommends ffmpeg git libsndfile1 \
    && rm -rf /var/lib/apt/lists/*
COPY backend/requirements.txt backend/requirements-cloud.txt /app/backend/
# Install the CPU wheel explicitly. The default Linux PyTorch package may pull
# several gigabytes of CUDA libraries even though the Cloud Run worker is CPU-only.
RUN python -m pip install --no-cache-dir \
      --index-url https://download.pytorch.org/whl/cpu torch==2.8.0 \
    && python -m pip install --no-cache-dir -r backend/requirements-cloud.txt \
    && python -c "from huggingface_hub import hf_hub_download; hf_hub_download(repo_id='xavriley/midi-transcription-models', filename='filosax_25k.pth')"
COPY backend/ /app/backend/
COPY --from=frontend-build /app/frontend/dist /app/frontend/dist
CMD ["sh", "-c", "uvicorn backend.app:app --host 0.0.0.0 --port ${PORT}"]
