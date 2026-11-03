# Self-hosting image, published to the GitHub Container Registry:
#     docker pull ghcr.io/arkalogy/bppicker
# Two ways to use it (README "Docker" section has the full commands):
#   * headless: `bpp run` picks the best photos from a mounted folder and
#     copies them to another mounted folder; no browser needed;
#   * the full app: `bpp serve` (the default command), published to the
#     host's loopback only, opened with the keyed link it prints, and
#     reached from another computer through an SSH tunnel.
# Build it yourself with `docker build -t bppicker .`.
#
# The image installs only pip wheels (no compiler): the web, HEIC and
# camera-RAW extras plus the wheel-only part of the `faces` extra
# (mediapipe, ai-edge-litert, scipy), which the pose, expression,
# segmentation and BlazeFace scorers need. A test keeps that list equal
# to `faces` in pyproject.toml. Only dlib / face_recognition are left out:
# they need a compiler, and their bundled landmark model has
# non-commercial training-data terms, so this image does not
# redistribute it. The default face models (YuNet / SFace) download at
# first use after the license prompt, exactly as in the app; the only
# model files in the image are the two small ones that ship inside the
# packages (bpp's BlazeFace short-range and OpenCV's Haar cascade).
FROM python:3.11-slim

# libgl1 / libglib2.0-0: mediapipe pulls in the full (non-headless)
# opencv-contrib-python wheel, which loads them at import time.
# libegl1 / libgles2: mediapipe's native Tasks library links EGL and
# GLES; without them every mediapipe scorer fails to load.
RUN apt-get update && apt-get install -y --no-install-recommends \
    libgl1 libglib2.0-0 libegl1 libgles2 && \
    rm -rf /var/lib/apt/lists/*

WORKDIR /app
# Dependencies first, in a layer that depends only on pyproject.toml, so
# a code change doesn't reinstall them: install against an empty
# package, then drop it.
# (README.md is a stub here, so a docs edit doesn't bust this layer.)
COPY pyproject.toml ./
RUN mkdir bpp && touch bpp/__init__.py README.md \
    && pip install --no-cache-dir ".[web,heic,raw]" \
        "scipy>=1.10" "mediapipe>=0.10" "ai-edge-litert>=2.1" \
    && pip uninstall -y bppicker && rm -rf bpp README.md
COPY README.md ./
COPY bpp/ bpp/
RUN pip install --no-cache-dir --no-deps . && rm -rf /app/bpp

# Fail the build, not the user, if a native library is missing: import
# every optional scorer package, then load mediapipe's Tasks library the
# way the app does (cpu_support._PROBE). mediapipe goes last: exit 132
# (SIGILL) means the builder has no AVX (an emulated amd64 build), which
# the app checks for at run time, so only warn, with everything else
# already checked.
RUN python -c "import cv2, scipy, ai_edge_litert.interpreter, pillow_heif, rawpy, onnxruntime, flask, bpp" \
    && python -c "from bpp.scoring.cpu_support import _PROBE; exec(_PROBE)"; \
    rc=$?; if [ "$rc" -eq 132 ]; then echo "warning: no AVX here; mediapipe load not checked"; \
    elif [ "$rc" -ne 0 ]; then exit "$rc"; fi

# License notices travel with the image. NOTICE.txt lists the GPL / LGPL
# native libraries the wheels bundle and the written offer for their
# source.
COPY LICENSE NOTICE.txt /usr/share/doc/bppicker/

# Non-root user — defence-in-depth if an RCE is found
RUN useradd --create-home --shell /bin/false bpp \
    && mkdir -p /data && chown bpp /data
USER bpp

# Keep downloaded models and model acceptances on the volume so they
# survive container re-creation (default would be the container's home).
# HOME too: run with `--user "$(id -u):$(id -g)"` the uid has no passwd
# entry, and HOME would fall back to `/`, which the export and
# open-folder path checks would then treat as the home folder.
# PYTHONNOUSERSITE: the image's packages are fixed; without it an in-app
# install would land in /data/.local and shadow them across upgrades.
ENV HOME=/data \
    BPP_CACHE_DIR=/data/.cache/bpp \
    XDG_CONFIG_HOME=/data/.config \
    PYTHONNOUSERSITE=1

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
# start-period covers the processor check at start-up (cpu_support: up to
# 120 s on a slow NAS) before the server listens.
HEALTHCHECK --interval=30s --timeout=5s --start-period=150s --retries=3 \
    CMD python3 -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:5001/static/manifest.json', timeout=4)" || exit 1

VOLUME /data

# Trust the Docker bridge gateway range AND Docker Desktop's host
# alias range. Requests whose immediate peer IP is in these ranges are
# treated exactly like loopback: like a local browser, they still need
# the boot key (below) before the page hands over the owner token. That
# is what lets you use your own container from the host browser, because
# host traffic reaches the container from the bridge gateway, not from
# 127.0.0.1. The ranges also cover every other container on the same
# Docker network, which is why the boot key stays on. (X-Forwarded-For is NOT consulted here; that additionally
# needs `behind_proxy: true` in the config — see bpp/commands/serve.py.)
#
# CRITICAL: this is only safe if the container port is published to
# host loopback:
#     docker run -p 127.0.0.1:5001:5001 -v /path/to/library:/data <image>
# If you publish to all interfaces (`-p 5001:5001`), other machines on
# your network can reach the port, and their traffic can also arrive
# from an address inside these trusted ranges (e.g. via Docker
# Desktop's port forwarder): only the boot key would stand between
# them and owner access. LAN Sharing (phone access with device pairing) is therefore
# not a supported container setup: with a loopback-only publish phones
# can't reach the port, and with an all-interfaces publish whether LAN
# clients look like the trusted gateway depends on the Docker platform.
# For phone access, run bpp natively and use LAN Sharing there.
ENV BPP_TRUSTED_PROXIES=172.16.0.0/12,192.168.65.0/24

# Bind 0.0.0.0 INSIDE the container so Docker's port mapping can reach
# the server (a loopback bind inside the container is unreachable from
# the host). Exposure to the outside world is decided by the `-p` flag
# above, not by this bind.
# No --open-loopback: the server prints a keyed link
# (http://127.0.0.1:5001/?_boot=...) to `docker logs`, or takes a fixed
# key from BPP_BOOT_SECRET. Other containers and local users who can
# reach the port get a locked page without it.
CMD ["bpp", "serve", "--library", "/data", "--host", "0.0.0.0", "--no-browser"]
