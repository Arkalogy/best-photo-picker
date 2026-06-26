# Configuration: environment variables

bpp reads the following `BPP_*` environment variables at runtime.
Defaults are tuned for the standalone Tauri app on a personal Mac;
the rest are intended for headless / Docker / NAS / reverse-proxy
deployments. None of them affect the threat model when unset.

For the trust model and auth boundary that some of these variables
interact with (notably `BPP_TRUSTED_PROXIES`), see
[`docs/security.md`](security.md).

## `BPP_TRUSTED_PROXIES`

**Default:** *(unset)* — only `request.remote_addr` (the direct TCP
peer) is consulted for the loopback / LAN gate. No proxy hop.

**Type:** comma-separated list of CIDR ranges.

**What it does:** when bpp sits behind a reverse proxy (nginx,
Caddy, Traefik) on the same host, the direct peer is the proxy
itself (typically `127.0.0.1` or a Docker bridge gateway like
`172.17.0.1`). With this var set, requests whose `request.remote_addr`
matches any listed CIDR have their `X-Forwarded-For` header trusted,
and the *original* client IP is used for the loopback / LAN auth gate
instead.

**Safety filter:** `_is_safe_proxy_network()` (in `bpp/web/share.py`)
**rejects** any CIDR that contains public addresses — `0.0.0.0/0`,
`::/0`, or arbitrary public ranges. The only ranges that pass:
loopback (`127/8`, `::1`), RFC1918 private (`10/8`, `172.16/12`,
`192.168/16`), link-local (`169.254/16`, `fe80::/10`), and the
Docker-Desktop alias range. A misconfigured `0.0.0.0/0` would
otherwise hand the owner SPA (with the per-boot app token) to any
remote — that's why parse-time rejection logs a warning and drops
the entry rather than honoring it. See `tests/test_trust_proxy.py`
for the matrix.

**Example:**
```sh
# nginx on the same host:
BPP_TRUSTED_PROXIES=127.0.0.1/32

# Inside Docker, behind a host-network reverse proxy:
BPP_TRUSTED_PROXIES=172.16.0.0/12,192.168.65.0/24
```

## `BPP_TRUST_PROXY` (deprecated)

**Default:** *(unset)*.

**Status:** **deprecated and ignored.** The original boolean
`BPP_TRUST_PROXY=1` flag was a "trust any X-Forwarded-For from any
peer" toggle — fine on a single-host nginx setup but a privilege-
escalation primitive everywhere else. Replaced by the explicit
CIDR allowlist `BPP_TRUSTED_PROXIES`.

**Migration:** if you previously set `BPP_TRUST_PROXY=1`, switch to
`BPP_TRUSTED_PROXIES=<cidr-list>` with the explicit ranges your
proxy actually lives in. bpp logs a warning at startup if
`BPP_TRUST_PROXY` is still set, pointing here.

## `BPP_CACHE_DIR`

**Default:** `${XDG_CACHE_HOME:-~/.cache}/bpp` (Linux/macOS XDG
convention; falls back to `~/.cache/bpp` if `XDG_CACHE_HOME` unset).

**Type:** filesystem path. `~` is expanded.

**What it does:** roots the directory tree where bpp stores
downloaded ML weights, CLIP image embeddings, tokenizer vocab files,
and other per-host caches. Useful for:

- **Docker volumes:** mount a single named volume at a stable path
  (`-v bpp-cache:/var/lib/bpp/cache`) and point `BPP_CACHE_DIR` at it.
- **NAS / external drive:** put the multi-GB models on an SSD that
  isn't the boot drive.
- **CI:** ephemeral `BPP_CACHE_DIR=/tmp/bpp-test-cache` per-run.

Resolution lives in `cache_dir()` (`bpp/utils/paths.py`).

## `BPP_MODELS_DIR`

**Default:** `${BPP_CACHE_DIR}/models` — the `models/` subdir of the
cache root.

**Type:** filesystem path. `~` is expanded.

**What it does:** overrides *just* the models subdirectory without
moving the rest of the cache. Useful when:

- The host has a small SSD for the cache but a large HDD for the
  500MB+ ML weights.
- A site-shared model directory is mounted read-only across multiple
  bpp instances (and the cache stays per-instance for write-heavy
  CLIP embeddings).

If both `BPP_CACHE_DIR` and `BPP_MODELS_DIR` are set, models go to
`BPP_MODELS_DIR` and everything else goes under `BPP_CACHE_DIR`.
Resolution lives in `models_dir()` (`bpp/utils/paths.py`).

## `BPP_ENABLE_PLUGINS`

**Default:** *(unset)* — plugin loading is **off**. `bpp.plugins` entry-points
in the venv are ignored.

**Type:** boolean string. Accepted truthy values: `1`, `true`, `yes`, `on`
(case-insensitive). Everything else (including empty / unset) means disabled.

**What it does:** when set to a truthy value, bpp loads every Python package
that declares a `[project.entry-points."bpp.plugins"]` entry-point at process
startup. Each entry-point's `setup()` callable is invoked once, allowing
third-party packages to register face detectors, embedders, config fields,
smart-album types, and background workers without forking the codebase.

**Trust contract:** a `bpp.plugins` entry-point runs **arbitrary Python code**
in the bpp process with the same privileges as bpp itself — filesystem access,
network access, your photo library and model weights. Plugins are not sandboxed.
Treat plugin packages the way you'd treat editor extensions: install only from
sources you trust. The opt-in flag exists so a malicious package that lands in
your venv (typo-squat, compromised transitive dep, accidental install) cannot
auto-execute on the next `bpp serve`.

See [`docs/plugins.md`](plugins.md) for the authoring guide and extension-point
list, and the reference implementation in `examples/plugin_example/`.

## `BPP_ONNX_PROVIDERS`

**Default:** *(unset)* — equivalent to `CPUExecutionProvider`. Every ONNX
session in bpp (SCRFD face detection, CLIP visual + text encoders, YOLOv11n
pet detection) constructs with CPU-only inference, exactly as in pre-helper
versions.

**Type:** comma-separated list of ONNX Runtime execution-provider names, in
priority order. Whitespace around commas tolerated. Provider names are
passed to `onnxruntime.InferenceSession(providers=...)` after filtering
against what the installed wheel actually supports.

**What it does:** opts the user into hardware acceleration for the three
ONNX models without rebuilding the package. Unknown / typo'd names log a
warning and are dropped; CPUExecutionProvider is always appended as the
final fallback so the session can still load when the requested provider
isn't compiled into the wheel.

```sh
# Apple Silicon (CoreML / Apple Neural Engine)
export BPP_ONNX_PROVIDERS="CoreMLExecutionProvider,CPUExecutionProvider"

# NVIDIA GPU (Linux) — also requires `pip install onnxruntime-gpu`
export BPP_ONNX_PROVIDERS="CUDAExecutionProvider,CPUExecutionProvider"

# Windows DirectML
export BPP_ONNX_PROVIDERS="DmlExecutionProvider,CPUExecutionProvider"
```

**Trust / safety implications:** a hardware-accelerated provider is a
different inference backend with its own correctness profile. CoreML on
some ONNX Runtime versions returns subtly different embeddings than CPU
for the same inputs — face clusters generated under one provider may not
match the other. CUDA can crash session construction if the runtime/driver
combo is wrong. **The accelerated paths are not exercised in upstream CI**
(we run CPU only on every push). Reports of working configurations are
welcome via GitHub issues; treat anything you opt into as your own
verification surface until it lands in the README's "Tested in CI" matrix.

The helper lives in `bpp.scoring.onnx_providers.get_providers()`. All three
session-creation sites (`face_scrfd`, `clip_embed`, `pets`) route through it,
guarded by `tests/test_onnx_providers.py`.

## `BPP_CLIP_MAX_PHOTOS`

**Default:** *(unset)* — `CLIP_EMBEDDING_MAX_ROWS = 200_000`. Libraries with
more CLIP-indexed photos refuse to load the embedding matrix into RAM and
skip CLIP-based semantic dedupe rather than risk an OOM kill of the server.

**Type:** positive integer (digits only). Non-numeric values are ignored
and the default is used.

**What it does:** raises the in-memory cap on the CLIP embedding matrix.
Each CLIP embedding is 512 × float32 ≈ 2 KB, so the default cap costs
~400 MB for the dict plus another ~400 MB for the `np.stack` matrix during
similarity search (~800 MB total at 200K photos). On a 16 GB machine,
`BPP_CLIP_MAX_PHOTOS=500000` is a reasonable lift; on a 32 GB machine,
`1000000`. Above the cap, `ClipEmbeddingsTooLarge` is raised and the
caller (`WebAppState.load_clip_embeddings`) logs a helpful message and
degrades gracefully — the rest of the app keeps working, only CLIP
semantic dedupe is skipped.

**Trust / safety implications:** the cap is a memory safety knob, not a
security boundary. Setting it above what the host can hold will OOM-kill
the server when a CLIP-driven endpoint runs — a denial-of-service against
yourself. There's no auth-bypass or data-exposure dimension here.

The reader lives in `bpp/db/clip.py` at module import; restart the server
after changing the var.

## `BPP_FACE_MAX_PHOTOS`

**Default:** *(unset)* — `FACE_EMBEDDINGS_MAX_ROWS = 500_000`. Libraries with
more face-embedding rows refuse to load the full matrix and short-circuit
face-cluster mutation flows (notably `api_faces_restore`) with a
user-friendly `FaceEmbeddingsTooLarge` error rather than risk an OOM kill.

**Type:** positive integer (digits only). Non-numeric values are ignored
and the default is used.

**What it does:** raises the in-memory cap on face-embedding loads. Each
SFace embedding is 128 × float32 ≈ 512 bytes. Peak memory is matrix + dict
+ an (N, M) distance buffer ≈ 3× the matrix; on a 1M-face library that's
~1.5 GB before the OS kills the process. On a 16 GB machine the default
500K cap costs ~750 MB peak; bumping to `BPP_FACE_MAX_PHOTOS=1000000` is
reasonable on 32 GB.

Per-library bypass: users can also opt out per library via Settings → Faces
(the DB-level `face_max_override = "bypass"` setting). Tauri users get that
path; CLI / dev users get the env var.

**Trust / safety implications:** memory safety knob, not a security
boundary. Setting it above what the host can hold OOM-kills the server
when a face-mutation endpoint runs — denial-of-service against yourself.
No auth-bypass or data-exposure dimension.

The reader lives in `bpp/db/face_queries.py` at module import; restart the
server after changing the var.

## `BPP_ACCEPTANCE_LOG_PATH`

**Default:** *(unset)* — falls back to
`${XDG_CONFIG_HOME:-~/.config}/bpp/model-acceptance.jsonl`.

**Type:** filesystem path. Parent directory is created on first write.

**What it does:** redirects the restricted-license-model acceptance log to
a different path. Each row is one JSON object capturing the user's
click-through response (model id, acknowledgment-text hash, use context,
separate-rights assertion, optional source-of-rights note, ISO timestamp,
schema version). The log is local-only and never transmitted; the
override exists so tests and dev workflows can isolate their writes
without touching the real user config directory.

**Trust / safety implications:** evidentiary record. Moving the path is
fine; deleting it loses the ability to prove which model acknowledgments
the user accepted. Don't point it at a multi-user-writable location.

The reader lives in `bpp/registry/acceptance_log.py`.

## `BPP_USE_CONTEXT_PATH`

**Default:** *(unset)* — falls back to
`${XDG_CONFIG_HOME:-~/.config}/bpp/use-context.json`.

**Type:** filesystem path. Parent directory is created on first write.

**What it does:** redirects the user's declared use-context record. The
file stores the current declaration (`personal` / `research` /
`commercial` / `unspecified`), the ISO timestamp it was set, the
`set_via` label (`first-launch-gate`, `settings`, `cli`, `test`), and an
audit trail of older declarations. Local-only and never transmitted. The
override exists so tests and dev workflows can isolate their writes
without touching the real user config directory.

**Trust / safety implications:** the hard-block decision for restricted
models reads from this file. Moving the path is fine; pointing it at a
multi-user-writable location would let another user on the same machine
change the declared use context and unlock restricted-model loads under a
context the actual user did not declare.

The reader lives in `bpp/registry/use_context_store.py`.

## `BPP_BYOM_PATH`

**Default:** *(unset)* — falls back to
`${XDG_CONFIG_HOME:-~/.config}/bpp/byom-models.json`.

**Type:** filesystem path. Parent directory is created on first write.

**What it does:** redirects the Bring-Your-Own-Model (BYOM) store. Each
entry in the file points BPP at a user-supplied model file, along with
the file's SHA-256 at registration time, a user-friendly display name,
and pointers to the BYOM acknowledgment text the user accepted. Local-only
and never transmitted; the override exists so tests and dev workflows
can isolate their writes without touching the real user config
directory.

**Trust / safety implications:** evidentiary record for which local
files the user pointed BPP at. The store does not contain the user's
weights — just paths and hashes. Moving the path is fine; pointing it at
a multi-user-writable location lets another user on the same machine
register their own model paths under your identity.

The reader lives in `bpp/registry/byom.py`.

## `BPP_DISABLE_REMOTE_REGISTRY`

**Default:** *(unset)* — the remote-registry overlay fetch runs at
import time of `bpp.registry`.

**Type:** any truthy string (`"1"`, `"true"`, etc.) disables the fetch;
unset or empty leaves it on.

**What it does:** turns off the Batch-8 remote-registry overlay
mechanism entirely. With the variable set, BPP only sees the bundled
baseline registered by `bpp.registry.builtins`; no HTTPS call is made
on startup. Useful for CI runs (deterministic), air-gapped deployments
(no network), and unit tests that need a stable in-process registry.

**Trust / safety implications:** the overlay is BPP's mechanism for
applying upstream status changes (`legally_blocked` after a takedown,
new entries published by the maintainer) without shipping a new BPP
release. Disabling it freezes the user on whatever the bundled baseline
shipped — safer in the sense of "no network exposure," less responsive
to upstream-driven security updates.

The reader lives in `bpp/registry/__init__.py`.

## `BPP_REMOTE_REGISTRY_TIMEOUT`

**Default:** *(unset)* — the remote-registry fetch uses a 10-second
network timeout.

**Type:** seconds as a number (decimal allowed).

**What it does:** overrides the HTTPS-fetch timeout used by
`bpp.registry.remote_registry.fetch_remote_manifest`. Below 1 second is
typically too aggressive even for fast networks; above 60 seconds
defeats the purpose of having a timeout at all.

**Trust / safety implications:** none on its own. The override exists
for test scenarios that need to assert the timeout path, and for users
on very slow networks who want to give the fetch more headroom before
falling back to the bundled baseline.

The reader lives in `bpp/registry/remote_registry.py`.

## `BPP_REMOTE_REGISTRY_URL`

**Default:** *(unset)* — the remote-registry fetch uses
`https://arkalogy.github.io/bppicker-registry/registry.json`.

**Type:** an HTTPS URL (or `http://` URL when
`BPP_REMOTE_REGISTRY_INSECURE=1` is also set).

**What it does:** overrides the URL the overlay fetch reads from.
Used for local mocking (see `scripts/run_mock_registry.py`) and for
air-gapped deployments that mirror the manifest on an internal host.
The HTTPS-only + host-allowlist rules still apply unless the insecure
escape hatch is also set — and the signature check stays mandatory
regardless of where the manifest comes from.

**Trust / safety implications:** the override moves the source of the
manifest. A misconfigured URL pointing at an attacker-controlled host
still cannot inject malicious entries — every manifest must verify
against the bundled trusted-key set, which the override does NOT
change. The cryptographic boundary holds; the URL override only
changes the transport.

The reader lives in `bpp/registry/remote_registry.py`.

## `BPP_REMOTE_REGISTRY_INSECURE`

**Default:** *(unset)* — `http://` URLs are refused and only hosts
on `ALLOWED_HOSTS` are accepted.

**Type:** any truthy string (`"1"`, `"true"`, etc.) enables insecure
mode; unset or empty leaves it off.

**What it does:** allows `http://` URLs and bypasses the host
allowlist. Intended for local mocking only — when set, the fetch
emits a WARNING log line every time it runs so an accidental
production-leak is impossible to miss.

**Trust / safety implications:** the **signature check stays
mandatory** even in insecure mode. A tampered manifest still fails
verification and falls back to the bundled baseline. The
transport-layer rules (HTTPS, host allowlist) are belt-and-
suspenders for production; the cryptographic signature is the actual
security boundary. The escape hatch exists so a developer can spin
up a mock registry on `http://127.0.0.1:9088/registry.json` without
provisioning a TLS cert.

The reader lives in `bpp/registry/remote_registry.py`.
