# ==============================================================================
# Multi-stage Dockerfile for Void Cut (React + Vite + FastAPI + FFmpeg)
# ==============================================================================

# ── Stage 1: Build React Frontend ─────────────────────────────────────────────
FROM node:20-alpine AS frontend-builder
WORKDIR /app

# Install dependencies
COPY package.json package-lock.json ./
RUN npm ci

# Copy source and build production bundle
COPY . .
RUN npm run build

# ── Stage 2: Runtime Environment (FastAPI + FFmpeg + Python) ──────────────────
FROM python:3.11-slim AS runner
WORKDIR /app

# Set environment variables
ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PORT=8000 \
    HOST=0.0.0.0

# Install system dependencies (FFmpeg, essential libs for OpenCV/video processing)
RUN apt-get update && apt-get install -y --no-install-recommends \
    ffmpeg \
    libgl1 \
    libglib2.0-0 \
    curl \
    && rm -rf /var/lib/apt/lists/*

# Install Python dependencies (use PyTorch CPU to keep image size small & fast on 4GB RAM)
COPY requirements.txt ./
RUN pip install --no-cache-dir --upgrade pip && \
    pip install --no-cache-dir torch --index-url https://download.pytorch.org/whl/cpu && \
    pip install --no-cache-dir -r requirements.txt

# Copy backend code
COPY backend ./backend

# Copy built frontend dist from Stage 1
COPY --from=frontend-builder /app/dist ./dist

# Create runtime directories for temp, uploads, exports, fonts
RUN mkdir -p temp exports uploads fonts

# Expose port
EXPOSE 8000

# Healthcheck
HEALTHCHECK --interval=30s --timeout=10s --start-period=15s --retries=3 \
  CMD curl -f http://localhost:8000/api/system-stats || exit 1

# Start Uvicorn ASGI Server
CMD ["python", "-m", "uvicorn", "backend.main:app", "--host", "0.0.0.0", "--port", "8000"]
