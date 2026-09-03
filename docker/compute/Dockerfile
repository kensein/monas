# MONAS compute image — build on PC (online), load on litbangweb (offline).
# Target: Ubuntu host 16.04+ with Docker; runtime is modern Linux in container.
FROM python:3.11-slim-bookworm

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PYTHONPATH=/app \
    PIP_DISABLE_PIP_VERSION_CHECK=1

RUN apt-get update && apt-get install -y --no-install-recommends \
        build-essential \
        libhdf5-dev \
        libnetcdf-dev \
        curl \
        openssh-client \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

COPY requirements.txt /app/requirements.txt
RUN pip install --no-cache-dir -r requirements.txt \
    && pip install --no-cache-dir python-dotenv

COPY backend /app/backend
COPY scripts /app/scripts
COPY docker/compute/entrypoint.sh /app/entrypoint.sh
RUN chmod +x /app/entrypoint.sh /app/scripts/*.sh || true

# Persist DB + artifacts via volume mount at /app/data
RUN mkdir -p /app/data/nc /app/data/obs /app/data/artifacts /app/logs

ENTRYPOINT ["/app/entrypoint.sh"]
CMD ["verify"]
