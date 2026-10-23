"""A child's deliberate refusal, written for the user, reaches the
progress stream instead of a generic "crashed; see server.log"."""

from __future__ import annotations

import queue

from bpp.utils.subprocess_runner import SENTINEL, BoundedSubprocessRunner


class _Phase:
    name = "Face extraction"

    def initial_state(self):
        return {}

    def reduce(self, state, msg):
        return state, None


class _Proc:
    pid = 1

    def is_alive(self):
        return False

    exitcode = 0


def _drain(messages):
    runner = BoundedSubprocessRunner.__new__(BoundedSubprocessRunner)
    runner.phase = _Phase()
    runner.message_timeout_s = 1
    q: queue.Queue = queue.Queue()
    for m in messages:
        q.put(m)
    events: list = []
    runner._drain(q, _Proc(), events.append)
    return events


def test_a_refusal_written_for_the_user_is_forwarded_verbatim():
    words = "Face analysis skipped: accept the model's license in Settings → Models."
    events = _drain(
        [
            {"type": "fatal_error", "error": words, "user_message": words, "traceback": "tb"},
            SENTINEL,
        ]
    )
    assert [e for e in events if e["type"] == "error"] == [{"type": "error", "message": words}]


def test_an_unexplained_crash_keeps_the_generic_message():
    events = _drain(
        [{"type": "fatal_error", "error": "KeyError: 'x'", "traceback": "tb"}, SENTINEL]
    )
    assert events == [
        {"type": "error", "message": "Face extraction crashed; see server.log for the error"}
    ]


def test_the_face_child_payload_carries_the_user_message():
    from bpp.errors import BppError
    from bpp.web.analyze_face_extract import fatal_error_payload

    words = "Face analysis skipped: accept the model's license."
    payload = fatal_error_payload(BppError(words, user_message=words))
    assert payload["type"] == "fatal_error"
    assert payload["user_message"] == words
    assert payload["error"] == words
    plain = fatal_error_payload(KeyError("x"))
    assert plain["user_message"] is None
    assert "x" in plain["error"]
