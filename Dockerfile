# Self-hosting image — build it yourself:
#     docker build -t bppicker .
#     docker run -p 127.0.0.1:5001:5001 -v /path/to/library:/data bppicker
# No prebuilt image is published (there is nothing to `docker pull`), and
# this Dockerfile is not built in CI: the `Publish Docker image` workflow
# is manual-only and has not been run. Treat it as untested.

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

RUN pip install --no-cache-dir --prefix=/install ".[web,faces,heic]"


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
RUN useradd --create-home --shell /bin/false bpp \
    && mkdir -p /data && chown bpp /data
USER bpp

# Keep downloaded models and model acceptances on the volume so they
# survive container re-creation (default would be the container's home).
ENV BPP_CACHE_DIR=/data/.cache/bpp \
    XDG_CONFIG_HOME=/data/.config

EXPOSE 5001

# Liveness probe. It must hit a path the auth gate lets through WITHOUT a
# token: every /api/* route (including /api/v1/health) requires the
# per-boot app session token, which only the rendered index page carries,
# so probing /api/v1/health would get a 403 and mark the container
# unhealthy forever. /static/* is public by design (see
# bpp/web/share.py:authorize_request), a static file contains no secrets,
# and the request still runs the full Flask middleware (auth gate + DB
# connection), so a wedged server fails the probe. The probe originates
# inside the container (127.0.0.1 = loopback), so the LAN gate never
# blocks it. Pinned by tests/test_docker_healthcheck.py.
HEALTHCHECK --interval=30s --timeout=5s --start-period=20s --retries=3 \
    CMD python3 -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:5001/static/manifest.json', timeout=4)" || exit 1

VOLUME /data

# Trust the Docker bridge gateway range AND Docker Desktop's host
# alias range. Requests whose immediate peer IP is in these ranges are
# treated exactly like loopback — i.e. as the owner: they get the owner
# SPA with the per-boot app session token, and full API access. That is
# what lets you use your own container from the host browser, because
# host traffic reaches the container from the bridge gateway, not from
# 127.0.0.1. (X-Forwarded-For is NOT consulted here; that additionally
# needs `behind_proxy: true` in the config — see bpp/commands/serve.py.)
#
# CRITICAL: this is only safe if the container port is published to
# host loopback:
#     docker run -p 127.0.0.1:5001:5001 -v /path/to/library:/data <image>
# If you publish to all interfaces (`-p 5001:5001`), other machines on
# your network can reach the port, and their traffic can also arrive
# from an address inside these trusted ranges (e.g. via Docker
# Desktop's port forwarder) — they would get owner access with no
# pairing. LAN Sharing (phone access with device pairing) is therefore
# not a supported container setup: with a loopback-only publish phones
# can't reach the port, and with an all-interfaces publish whether LAN
# clients look like the trusted gateway depends on the Docker platform.
# For phone access, run bpp natively and use LAN Sharing there.
ENV BPP_TRUSTED_PROXIES=172.16.0.0/12,192.168.65.0/24

# Bind 0.0.0.0 INSIDE the container so Docker's port mapping can reach
# the server (a loopback bind inside the container is unreachable from
# the host). Exposure to the outside world is decided by the `-p` flag
# above, not by this bind. Access via http://localhost:5001 on the host.
CMD ["bpp", "serve", "--library", "/data", "--host", "0.0.0.0", "--no-browser"]
