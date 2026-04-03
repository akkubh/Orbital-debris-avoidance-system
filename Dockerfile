# ─────────────────────────────────────────────────────────────────────────────
# Stage 1: Build the React/TypeScript frontend
# ─────────────────────────────────────────────────────────────────────────────
FROM node:20-alpine AS frontend-builder

WORKDIR /app/frontend

# Copy frontend dependency manifests first (layer-cache friendly)
COPY frontend/package.json frontend/package-lock.json* frontend/yarn.lock* ./

RUN npm install --frozen-lockfile 2>/dev/null || npm install

# Copy all frontend source
COPY frontend/ .

# Build production bundle → /app/frontend/dist
RUN npm run build


# ─────────────────────────────────────────────────────────────────────────────
# Stage 2: Python backend + serve pre-built frontend via FastAPI StaticFiles
# ─────────────────────────────────────────────────────────────────────────────
FROM python:3.11-slim AS backend

# System deps (sgp4 needs a C compiler only at install time; keep image slim)
RUN apt-get update && apt-get install -y --no-install-recommends \
        gcc \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# ── Python dependencies ───────────────────────────────────────────────────────
COPY backend/requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# ── Backend source ────────────────────────────────────────────────────────────
COPY backend/ .

# ── Frontend build artefacts → served as static files ────────────────────────
COPY --from=frontend-builder /app/frontend/dist ./static

# ── Persistent data volume mount point ───────────────────────────────────────
RUN mkdir -p /data
ENV ACM_DATA_DIR=/data

# ── Runtime config ────────────────────────────────────────────────────────────
# CORS: frontend is served from the same origin (port 8000), so localhost:5173
# is only needed during local dev.  Add extra origins via env override:
#   docker run -e ACM_CORS_ORIGINS="https://yourdomain.com" …
ENV ACM_CORS_ORIGINS="http://localhost:5173,http://localhost:3000,http://127.0.0.1:5173,http://127.0.0.1:3000"

EXPOSE 8000

# Mount /data as a named volume in docker-compose for state persistence
VOLUME ["/data"]

# Use uvicorn directly; --workers 1 keeps shared in-process state consistent
CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8000", "--workers", "1"]