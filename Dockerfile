FROM node:22-alpine AS frontend-build

WORKDIR /frontend

COPY frontend/package*.json ./
RUN npm ci

COPY frontend/ ./
RUN npm run build


FROM python:3.12-slim AS runtime

WORKDIR /app

RUN apt-get update \
    && apt-get install -y --no-install-recommends build-essential curl \
    && rm -rf /var/lib/apt/lists/*

COPY prd_pal/ ./prd_pal/
COPY README.md LICENSE NOTICE pyproject.toml main.py ./
RUN pip install --no-cache-dir .

COPY data/ ./data/
COPY --from=frontend-build /frontend/dist ./frontend/dist

ENV PYTHONUNBUFFERED=1

EXPOSE 8000

HEALTHCHECK --interval=30s --timeout=5s --start-period=20s --retries=3 \
  CMD curl -fsS http://127.0.0.1:8000/health || exit 1

CMD ["uvicorn", "prd_pal.server.app:app", "--host", "0.0.0.0", "--port", "8000"]
