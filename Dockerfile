# Stage 1: Builder
FROM --platform=linux/amd64 python:3.11-alpine AS builder

# Install build dependencies
RUN apk add --no-cache \
    gcc \
    musl-dev \
    libffi-dev \
    openssl-dev \
    cargo

WORKDIR /build

# Copy and install Python dependencies
COPY requirements.txt .
RUN pip install --no-cache-dir --prefix=/install \
    --no-warn-script-location \
    -r requirements.txt

# Stage 2: Runtime
FROM --platform=linux/amd64 python:3.11-alpine AS runtime

# Install minimal runtime dependencies
RUN apk add --no-cache \
    libffi \
    openssl \
    && rm -rf /var/cache/apk/*

WORKDIR /app

# Copy Python packages from builder
COPY --from=builder /install /usr/local

# Create non-root user
RUN addgroup -g 1000 appgroup && \
    adduser -u 1000 -G appgroup -s /bin/sh -D appuser

# Copy operator code
COPY --chown=appuser:appgroup observer-operator.py .

# Switch to non-root user
USER appuser

# Environment variables
ENV PYTHONUNBUFFERED=1 \
    PYTHONPATH=/usr/local/lib/python3.11/site-packages

# Health check
HEALTHCHECK --interval=30s --timeout=10s --start-period=5s --retries=3 \
    CMD python -c "import urllib.request; urllib.request.urlopen('http://localhost:8080/health')" || exit 1

# Run operator
CMD ["python", "observer-operator.py"]
