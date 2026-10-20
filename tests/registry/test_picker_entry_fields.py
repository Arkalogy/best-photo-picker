"""The picker entry carries what the JavaScript used to look up in maps
of the built-ins: the embedder's method value, the model's toggle key
and its legacy feature label. A plugin entry gets them the same way."""

from __future__ import annotations


def test_the_picker_entry_carries_toggle_key_and_method_value(registry_client):
    """The server says what the picker used to look up in its own maps."""
    groups = registry_client.get("/api/v1/model-registry/entries").get_json()["groups"]
    by_id = {e["id"]: e for g in groups for e in g["entries"]}
    assert by_id["sface_yunet"]["method_value"] == "sface"
    assert by_id["dlib_face_recognition_resnet_v1"]["method_value"] == "dlib"
    assert by_id["insightface_scrfd_25g"]["toggle_key"] == "model_scrfd"
    assert by_id["mediapipe_face_landmarker"]["toggle_key"] == "model_face_landmarker"
    assert by_id["opencv_yunet"]["toggle_key"] is None
    assert by_id["insightface_scrfd_25g"]["legacy_label"] == "SCRFD face detection"
    assert "toggle_key" in by_id["openai_clip_vit_b32_onnx"]
