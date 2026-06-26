# Stage 1: Build dlib and other compiled dependencies
FROM python:3.11-slim AS builder

RUN apt-get update && apt-get install -y --no-install-recommends \
    cmake build-essential libopenblas-dev liblapack-dev && \
    rm -rf /var/lib/apt/lists/*

WORKDIR /build

# Install compiled deps first (slow, cached separately)
RUN pip install --no-cache-dir --prefix=/install \
    dlib \
    face_recognition \
    && pip install --no-cache-dir --prefix=/install \
    "git+https://github.com/ageitgey/face_recognition_models@0.3.0"
# Pin face_recognition_models to the v0.3.0 tag instead of
# tracking HEAD. The repo is essentially abandoned (no commits in
# years), so pinning the historical tag locks the bytes the Docker
# build pulls. Keeps reproducibility intact even if the upstream
# branch changes or moves.

# Copy project and install it
COPY pyproject.toml README.md ./
COPY bpp/ bpp/

RUN pip install --no-cache-dir --prefix=/install ".[web,faces,nudity,heic]"


# Stage 2: Slim runtime
FROM python:3.11-slim

RUN apt-get update && apt-get install -y --no-install-recommends \
    libopenblas0 liblapack3 libgl1 libglib2.0-0 && \
    rm -rf /var/lib/apt/lists/*

# Copy installed packages from builder
COPY --from=builder /install /usr/local

# Copy source (for entry point)
COPY bpp/ /app/bpp/
COPY pyproject.toml README.md /app/
WORKDIR /app
RUN pip install --no-cache-dir -e "."

# Non-root user — defence-in-depth if an RCE is found
RUN useradd --no-create-home --shell /bin/false bpp \
    && mkdir -p /data && chown bpp /data
USER bpp

EXPOSE 5001

HEALTHCHECK --interval=30s --timeout=5s --start-period=20s --retries=3 \
    CMD python3 -c "import urllib.request; urllib.request.urlopen('http://localhost:5001/api/v1/health')" || exit 1

VOLUME /data

# Trust the Docker bridge gateway range AND Docker Desktop's host
# alias range. Requests from these IPs are treated as loopback (the
# user is on the host, not on the LAN), so the LAN gate doesn't lock
# the user out of their own container on first run.
#
# CRITICAL: this ONLY makes the Docker deployment safe if the
# container is published to host loopback:
#     docker run -p 127.0.0.1:5001:5001 ...
# If you publish to 0.0.0.0 (`-p 5001:5001`), LAN clients hitting
# the bridge gateway would also be inside the trusted CIDR — that's
# unsafe. For LAN access to your own container, enable LAN Sharing
# from inside the app instead; that flow has device pairing.
ENV BPP_TRUSTED_PROXIES=172.16.0.0/12,192.168.65.0/24

# Bind 0.0.0.0 inside container so Docker port mapping works.
# Users access via localhost:5001 on the host (after `-p
# 127.0.0.1:5001:5001`).
CMD ["bpp", "serve", "--library", "/data", "--host", "0.0.0.0", "--no-browser"]
