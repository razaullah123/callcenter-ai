# HMG voice agent — one image: voice server (/ws, /ws/voice-pipeline), control-plane API (/api), console (/console)

# ---- console build
FROM node:22-alpine AS console
WORKDIR /console
COPY console/package.json console/package-lock.json ./
RUN npm ci --no-audit --no-fund
COPY console/ ./
RUN npm run build

# ---- runtime
FROM python:3.11-slim
ENV PYTHONUNBUFFERED=1 PIP_NO_CACHE_DIR=1 PYTHONDONTWRITEBYTECODE=1
WORKDIR /app
RUN apt-get update && apt-get install -y --no-install-recommends libgomp1 curl && rm -rf /var/lib/apt/lists/*
COPY pyproject.toml ./
COPY runtime/__init__.py runtime/__init__.py
RUN pip install --upgrade pip && pip install .
COPY runtime/ runtime/
COPY skills/ skills/
COPY config/ config/
COPY db/ db/
COPY evals/ evals/
COPY scripts/ scripts/
COPY models/silero_vad.onnx models/silero_vad.onnx
COPY mcp_tools.json ./
COPY --from=console /console/dist console/dist
RUN pip install --no-deps -e . && useradd -m -u 10001 agent && mkdir -p logs models/phrases && chown -R agent /app
USER agent
EXPOSE 8080
HEALTHCHECK --interval=30s --timeout=5s --start-period=60s CMD curl -fs http://localhost:8080/health || exit 1
CMD ["python", "-m", "runtime.server", "8080"]
