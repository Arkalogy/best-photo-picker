#!/usr/bin/env python3
"""Export CLIP ViT-B/32 visual encoder to ONNX format.

Developer-only script. Requires: pip install open-clip-torch torch

Produces: ~/.cache/bpp/clip-vit-b-32-visual.onnx

This only needs to be run once. The exported ONNX model is then used at
runtime via onnxruntime (no torch dependency needed).
"""

from __future__ import annotations

import argparse
from pathlib import Path

import numpy as np
import open_clip
import torch


def export(output_dir: Path) -> None:
    output_dir.mkdir(parents=True, exist_ok=True)
    output_path = output_dir / "clip-vit-b-32-visual.onnx"

    print("Loading CLIP ViT-B/32 ...")
    model, _, _preprocess = open_clip.create_model_and_transforms("ViT-B-32", pretrained="openai")
    model.eval()

    # Extract visual encoder only
    visual = model.visual

    # Dummy input: batch of 1, 3x224x224
    dummy = torch.randn(1, 3, 224, 224)

    print(f"Exporting to {output_path} ...")
    torch.onnx.export(
        visual,
        dummy,
        str(output_path),
        input_names=["pixel_values"],
        output_names=["image_embeds"],
        dynamic_axes={"pixel_values": {0: "batch"}, "image_embeds": {0: "batch"}},
        opset_version=17,
    )

    # Verify
    import onnxruntime as ort

    sess = ort.InferenceSession(str(output_path), providers=["CPUExecutionProvider"])
    result = sess.run(None, {"pixel_values": dummy.numpy()})
    emb = result[0]
    print(f"Output shape: {emb.shape}, dtype: {emb.dtype}")
    print(f"Model size: {output_path.stat().st_size / 1024 / 1024:.1f} MB")

    # Sanity check: embedding should be non-zero
    norm = np.linalg.norm(emb)
    assert norm > 0, "Embedding norm is zero — export likely failed"
    print(f"Embedding norm: {norm:.4f} — export successful!")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=Path.home() / ".cache" / "bpp",
        help="Directory to write the ONNX model to",
    )
    args = parser.parse_args()
    export(args.output_dir)


if __name__ == "__main__":
    main()
