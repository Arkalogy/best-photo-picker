"""Owner-only endpoints, continued: host mutations, the face lock on auth
failure, share info / QR (split from test_owner_only_endpoints.py; same
TESTING=False real-auth setup)."""

from __future__ import annotations

import pytest

from tests.owner_only._owner import as_loopback


class TestHostMutationsBlocked:
    """~55 host-mutation endpoints across 11 blueprints
    were missing `@requires_local_app`. The CSP-style closed-list
    invariant ("every mutation gated") had drifted away from reality.
    These tests sweep the new gates so a future endpoint that forgets
    the decorator gets caught at PR time, not in a later review.

    Each endpoint asserts a 403 from a paired LAN device. Some
    endpoints validate input shape and short-circuit to 400 if the
    body is bad — but the decorator stack runs `@requires_local_app`
    BEFORE `@validate_json`, so a non-LOCAL_APP request always sees
    the 403 first."""

    def _lan_request(self, app, lan_device, method, path, **kw):
        client = app.test_client()
        token, fp = lan_device
        client.set_cookie("bpp_share_fp", fp)
        return client.open(
            path,
            method=method,
            headers={"X-Auth-Token": token},
            environ_overrides={"REMOTE_ADDR": "192.168.1.50"},
            **kw,
        )

    @pytest.mark.parametrize(
        "method,path,body",
        [
            # bp_albums (11)
            ("POST", "/api/v1/albums", {"name": "x"}),
            ("PUT", "/api/v1/albums/1", {"name": "y"}),
            ("DELETE", "/api/v1/albums/1", None),
            ("POST", "/api/v1/albums/1/recompute", {}),
            ("POST", "/api/v1/albums/1/override", {"filepath": "/x", "value": 1}),
            ("POST", "/api/v1/albums/1/favorite", {"filepath": "/x"}),
            ("POST", "/api/v1/albums/1/batch/override", {"filepaths": [], "value": 1}),
            ("POST", "/api/v1/albums/1/batch/favorite", {"filepaths": []}),
            ("POST", "/api/v1/albums/1/add-photos", {"filepaths": []}),
            ("POST", "/api/v1/albums/1/remove-photos", {"filepaths": []}),
            ("POST", "/api/v1/albums/refresh-smart", {}),
            # bp_tags (7)
            ("POST", "/api/v1/tags", {"name": "x"}),
            ("PUT", "/api/v1/tags/1", {"name": "y"}),
            ("DELETE", "/api/v1/tags/1", None),
            ("POST", "/api/v1/photos/1/tags", {"tag_id": 1}),
            ("DELETE", "/api/v1/photos/1/tags/1", None),
            ("POST", "/api/v1/tags/batch", {"tag_id": 1, "filepaths": []}),
            ("POST", "/api/v1/tags/batch/remove", {"tag_id": 1, "filepaths": []}),
            # bp_faces_manage (10)
            ("POST", "/api/v1/faces/merge", {}),
            ("POST", "/api/v1/faces/dismiss", {}),
            ("POST", "/api/v1/faces/split", {}),
            ("POST", "/api/v1/faces/restore", {}),
            ("POST", "/api/v1/faces/recluster", {}),
            ("POST", "/api/v1/faces/tag", {}),
            ("DELETE", "/api/v1/faces/tag", None),
            ("POST", "/api/v1/faces/reassign", {}),
            ("POST", "/api/v1/clip/extract", {}),
            ("DELETE", "/api/v1/faces/purge", None),
            # bp_faces_review (2)
            ("POST", "/api/v1/faces/review-pairs/verdict", {}),
            ("POST", "/api/v1/faces/review-pairs/verdict/undo", {}),
            # bp_pets (3)
            ("POST", "/api/v1/pets/split", {}),
            ("POST", "/api/v1/pets/merge", {}),
            ("POST", "/api/v1/pets/dismiss", {}),
            # bp_photos (6)
            ("POST", "/api/v1/recompute", {}),
            ("POST", "/api/v1/optimize", {}),
            ("POST", "/api/v1/override", {"filepath": "/x"}),
            ("POST", "/api/v1/favorite", {"filepath": "/x"}),
            ("POST", "/api/v1/batch/override", {"filepaths": []}),
            ("POST", "/api/v1/batch/favorite", {"filepaths": []}),
            # bp_core (3)
            ("POST", "/api/v1/photos/recheck-missing", {}),
            ("POST", "/api/v1/presets", {"name": "x", "config": {}}),
            ("DELETE", "/api/v1/presets/x", None),
            # bp_memories (1)
            ("POST", "/api/v1/memories/refresh", {}),
            # bp_analysis (3)
            ("POST", "/api/v1/analyze/cancel", {}),
            ("POST", "/api/v1/import/cancel", {}),
            ("POST", "/api/v1/compute-hashes", {}),
            # bp_photos_manage (11) — api_photos_delete / restore are
            # already exercised by the @validate_json migration tests,
            # but pin them here so the decorator stack stays right.
            ("POST", "/api/v1/photos/delete", {"filepaths": []}),
            ("POST", "/api/v1/photos/restore", {"filepaths": []}),
            ("POST", "/api/v1/photos/delete-permanent", {}),
            ("POST", "/api/v1/photos/hide", {}),
            ("POST", "/api/v1/photos/unhide", {}),
            ("POST", "/api/v1/photos/enhance", {}),
            ("POST", "/api/v1/photos/reset-edits", {}),
            ("POST", "/api/v1/photos/save-edits", {}),
            ("POST", "/api/v1/photos/1/date", {}),
            ("POST", "/api/v1/photos/1/inpaint", {}),
            ("GET", "/api/v1/photos/1/auto_straighten", None),
        ],
    )
    def test_endpoint_blocks_lan_device(self, app, lan_device, method, path, body):
        kw = {"json": body} if body is not None else {}
        r = self._lan_request(app, lan_device, method, path, **kw)
        assert r.status_code == 403, (
            f"{method} {path} should reject LAN device (got {r.status_code} {r.data!r})"
        )


# ─── Auth must run BEFORE @with_face_lock ────────────────────


class TestFaceLockNotAcquiredOnAuthFailure:
    """Face-mutating endpoints stack `@requires_local_app` and
    `@with_face_lock`. Decorators apply bottom-up so the order
    matters: pre-fix the inner decorator was `@requires_local_app`
    and the outer was `@with_face_lock`, meaning the lock was
    acquired BEFORE the auth check. A paired LAN device's request
    would 403, but only after parking on `ctx.face_op_lock` —
    spamming the endpoint from a malicious phone could DoS the owner
    by holding the lock against legitimate clustering / merge
    operations.

    Post-fix: `@requires_local_app` is the outer decorator. The
    auth gate returns 403 before `with_face_lock` ever attempts
    `ctx.face_op_lock.__enter__`. This test pins the contract by
    counting lock acquisitions during a non-LOCAL_APP request."""

    @pytest.fixture
    def counting_lock(self, app):
        """Replace ctx.face_op_lock with an instrumented lock that
        counts every __enter__. Restores the original on teardown."""
        ctx = app.extensions["bpp"]
        original = ctx.face_op_lock
        acquired = {"count": 0}

        class _CountingLock:
            def __enter__(self):
                acquired["count"] += 1
                return self

            def __exit__(self, *exc):
                return False

        ctx.face_op_lock = _CountingLock()
        try:
            yield acquired
        finally:
            ctx.face_op_lock = original

    @pytest.mark.parametrize(
        "method,path,body",
        [
            ("POST", "/api/v1/faces/merge", {}),
            ("POST", "/api/v1/faces/dismiss", {}),
            ("POST", "/api/v1/faces/split", {}),
            ("POST", "/api/v1/faces/restore", {}),
            ("POST", "/api/v1/faces/recluster", {}),
            ("POST", "/api/v1/faces/tag", {}),
            ("DELETE", "/api/v1/faces/tag", None),
            ("POST", "/api/v1/faces/reassign", {}),
            ("DELETE", "/api/v1/faces/purge", {"confirm": "purge"}),
            ("POST", "/api/v1/faces/review-pairs/verdict", {}),
        ],
    )
    def test_lan_request_does_not_acquire_face_lock(
        self, app, lan_device, counting_lock, method, path, body
    ):
        client = app.test_client()
        token, fp = lan_device
        client.set_cookie("bpp_share_fp", fp)
        kw = {"json": body} if body is not None else {}
        r = client.open(
            path,
            method=method,
            headers={"X-Auth-Token": token},
            environ_overrides={"REMOTE_ADDR": "192.168.1.50"},
            **kw,
        )
        assert r.status_code == 403, (
            f"{method} {path} should reject LAN device (got {r.status_code} {r.data!r})"
        )
        assert counting_lock["count"] == 0, (
            f"{method} {path} acquired ctx.face_op_lock "
            f"{counting_lock['count']} times BEFORE auth rejected — "
            "decorator order regressed; @requires_local_app must be "
            "outermost so a non-LOCAL_APP request never enters the "
            "lock."
        )


def test_face_endpoints_auth_decorator_is_outermost():
    """Source-scan complement to the runtime test above: every
    handler in bp_faces_manage.py / bp_faces_review.py that stacks
    `@with_face_lock` and `@requires_local_app` must list them in
    that order (auth outer, lock inner). Catches a future commit
    that swaps them by reflex."""
    from pathlib import Path

    for rel in ("bpp/web/bp_faces_manage.py", "bpp/web/bp_faces_review.py"):
        text = Path(rel).read_text()
        # `@with_face_lock\n@requires_local_app` is the BAD order
        # (auth inner, lock outer). Confirm zero occurrences.
        assert "@with_face_lock\n@requires_local_app" not in text, (
            f"{rel} has @with_face_lock above @requires_local_app — "
            "decorators apply bottom-up, so the lock is acquired "
            "BEFORE the auth check. Swap the order."
        )


# ─── /api/share/info GET ───────────────────────────────────────────


class TestShareInfoOwnerOnly:
    def test_lan_device_blocked(self, app, lan_device):
        client = app.test_client()
        token, fp = lan_device
        client.set_cookie("bpp_share_fp", fp)
        r = client.get(
            "/api/v1/share/info",
            headers={"X-Auth-Token": token},
            environ_overrides={"REMOTE_ADDR": "192.168.1.50"},
        )
        assert r.status_code == 403, f"share/info should reject LAN device (got {r.status_code})"

    def test_loopback_owner_allowed(self, app):
        r = as_loopback(app, "GET", "/api/v1/share/info")
        assert r.status_code == 200


# ─── /api/share/qr GET ────────────────────────────────────────────


class TestShareQrOwnerOnly:
    def test_lan_device_blocked(self, app, lan_device):
        client = app.test_client()
        token, fp = lan_device
        client.set_cookie("bpp_share_fp", fp)
        r = client.get(
            "/api/v1/share/qr",
            headers={"X-Auth-Token": token},
            environ_overrides={"REMOTE_ADDR": "192.168.1.50"},
        )
        assert r.status_code == 403, f"share/qr should reject LAN device (got {r.status_code})"

    def test_loopback_owner_allowed(self, app):
        # QR returns 404 when LAN sharing is off; 200/404 both fine from owner
        r = as_loopback(app, "GET", "/api/v1/share/qr")
        assert r.status_code in (200, 404), f"got {r.status_code}"
