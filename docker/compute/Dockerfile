# MONAS compute image — build on PC (online), load on litbangweb (offline).
# Target: Ubuntu host 16.04+ with Docker; runtime is modern Linux in container.
FROM python:3.11-slim-bookworm

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PYTHONPATH=/app \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    OPENBLAS_NUM_THREADS=1 \
    OMP_NUM_THREADS=1 \
    MKL_NUM_THREADS=1 \
    NUMEXPR_NUM_THREADS=1 \
    VECLIB_MAXIMUM_THREADS=1

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

# Strip CRLF (Windows builds) so shebang/entrypoint tidak gagal di Linux
RUN find /app -type f \( -name '*.sh' -o -name 'entrypoint.sh' \) -exec sed -i 's/\r$//' {} \; \
    && chmod +x /app/entrypoint.sh \
    && find /app/scripts -name '*.sh' -exec chmod +x {} \;

RUN mkdir -p /app/data/nc /app/data/obs /app/data/artifacts /app/logs

# Invoke via bash (aman meski shebang sempat CRLF)
ENTRYPOINT ["/bin/bash", "/app/entrypoint.sh"]
CMD ["verify"]
