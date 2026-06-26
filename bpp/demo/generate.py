"""Generate sample photos for demo mode."""

from __future__ import annotations

import math
import os
import random

from PIL import Image, ImageDraw, ImageFilter


def generate_sample_photos(outdir: str, count: int = 12, seed: int = 42) -> list[str]:
    """Generate varied sample photos and return their file paths.

    Creates visually interesting procedural images that exercise the full
    scoring pipeline (blur, exposure, composition, dedup).
    """
    os.makedirs(outdir, exist_ok=True)
    rng = random.Random(seed)
    paths: list[str] = []

    generators = [
        _sunset_gradient,
        _mountain_silhouette,
        _bokeh_circles,
        _geometric_pattern,
        _sky_gradient,
        _wave_pattern,
        _forest_silhouette,
        _starfield,
        _abstract_circles,
        _color_blocks,
        _radial_burst,
        _stripe_pattern,
    ]

    for i in range(count):
        gen = generators[i % len(generators)]
        img = gen(rng)
        # Add variety: some blurred, some over/under-exposed
        if i % 5 == 3:
            img = img.filter(ImageFilter.GaussianBlur(radius=4))
        if i % 7 == 4:
            img = img.point(lambda p: min(255, int(p * 1.6)))  # overexpose
        if i % 7 == 6:
            img = img.point(lambda p: int(p * 0.4))  # underexpose
        # Create a near-duplicate for dedup testing
        if i == 1:
            dup = img.copy()
            dup_path = os.path.join(outdir, "IMG_0000_dup.jpg")
            dup.save(dup_path, "JPEG", quality=92)
            paths.append(dup_path)
        name = f"IMG_{i + 1:04d}.jpg"
        path = os.path.join(outdir, name)
        img.save(path, "JPEG", quality=95)
        paths.append(path)

    return paths


def _sunset_gradient(rng: random.Random) -> Image.Image:
    """Warm sunset gradient with horizon line."""
    w, h = 1024, 768
    img = Image.new("RGB", (w, h))
    draw = ImageDraw.Draw(img)
    horizon = h * 2 // 3
    for y in range(h):
        if y < horizon:
            t = y / horizon
            r = int(30 + 200 * t)
            g = int(20 + 100 * t)
            b = int(80 + 60 * (1 - t))
        else:
            t = (y - horizon) / (h - horizon)
            r = int(230 - 180 * t)
            g = int(120 - 80 * t)
            b = int(20 + 30 * t)
        draw.line([(0, y), (w, y)], fill=(r, g, b))
    # Sun
    sx = w // 2 + rng.randint(-100, 100)
    draw.ellipse([sx - 40, horizon - 50, sx + 40, horizon + 30], fill=(255, 220, 100))
    return img


def _mountain_silhouette(rng: random.Random) -> Image.Image:
    """Mountain silhouette against a gradient sky."""
    w, h = 1024, 768
    img = Image.new("RGB", (w, h))
    draw = ImageDraw.Draw(img)
    for y in range(h):
        t = y / h
        r = int(100 + 80 * (1 - t))
        g = int(140 + 60 * (1 - t))
        b = int(180 + 50 * (1 - t))
        draw.line([(0, y), (w, y)], fill=(r, g, b))
    # Mountain polygon
    points = [(0, h)]
    peaks = rng.randint(3, 6)
    for i in range(peaks * 2 + 1):
        x = int(w * i / (peaks * 2))
        y_val = h - rng.randint(150, 450) if i % 2 == 1 else h - rng.randint(50, 150)
        points.append((x, y_val))
    points.append((w, h))
    draw.polygon(points, fill=(30, 40, 50))
    return img


def _bokeh_circles(rng: random.Random) -> Image.Image:
    """Soft bokeh circles on a dark background."""
    w, h = 1024, 768
    img = Image.new("RGB", (w, h), (15, 20, 35))
    draw = ImageDraw.Draw(img)
    for _ in range(rng.randint(20, 40)):
        x = rng.randint(0, w)
        y = rng.randint(0, h)
        r = rng.randint(20, 80)
        alpha = rng.randint(40, 120)
        color = (
            rng.randint(150, 255),
            rng.randint(100, 255),
            rng.randint(50, 200),
        )
        faded = tuple(c * alpha // 255 for c in color)
        draw.ellipse([x - r, y - r, x + r, y + r], fill=faded)
    img = img.filter(ImageFilter.GaussianBlur(radius=3))
    return img


def _geometric_pattern(rng: random.Random) -> Image.Image:
    """Colorful geometric grid pattern."""
    w, h = 1024, 768
    img = Image.new("RGB", (w, h), (240, 240, 235))
    draw = ImageDraw.Draw(img)
    cell = 64
    for row in range(h // cell + 1):
        for col in range(w // cell + 1):
            x = col * cell
            y = row * cell
            hue_shift = (row + col) * 15
            r = int(128 + 100 * math.sin(math.radians(hue_shift)))
            g = int(128 + 100 * math.sin(math.radians(hue_shift + 120)))
            b = int(128 + 100 * math.sin(math.radians(hue_shift + 240)))
            shape = (row + col) % 3
            if shape == 0:
                draw.rectangle([x + 4, y + 4, x + cell - 4, y + cell - 4], fill=(r, g, b))
            elif shape == 1:
                draw.ellipse([x + 4, y + 4, x + cell - 4, y + cell - 4], fill=(r, g, b))
            else:
                draw.polygon(
                    [(x + cell // 2, y + 4), (x + cell - 4, y + cell - 4), (x + 4, y + cell - 4)],
                    fill=(r, g, b),
                )
    return img


def _sky_gradient(rng: random.Random) -> Image.Image:
    """Blue to pink sky gradient with cloud-like blobs."""
    w, h = 1024, 768
    img = Image.new("RGB", (w, h))
    draw = ImageDraw.Draw(img)
    for y in range(h):
        t = y / h
        r = int(60 + 180 * t)
        g = int(120 + 50 * (1 - t))
        b = int(200 - 30 * t)
        draw.line([(0, y), (w, y)], fill=(r, g, b))
    for _ in range(rng.randint(5, 10)):
        cx = rng.randint(0, w)
        cy = rng.randint(0, h // 2)
        for _ in range(rng.randint(3, 6)):
            ox = cx + rng.randint(-60, 60)
            oy = cy + rng.randint(-20, 20)
            rx = rng.randint(30, 70)
            ry = rng.randint(15, 35)
            draw.ellipse([ox - rx, oy - ry, ox + rx, oy + ry], fill=(240, 240, 250))
    img = img.filter(ImageFilter.GaussianBlur(radius=6))
    return img


def _wave_pattern(rng: random.Random) -> Image.Image:
    """Sinusoidal wave pattern with ocean colors."""
    w, h = 1024, 768
    img = Image.new("RGB", (w, h), (10, 30, 60))
    draw = ImageDraw.Draw(img)
    n_waves = rng.randint(5, 8)
    for wave_i in range(n_waves):
        amp = rng.randint(20, 60)
        freq = rng.uniform(0.005, 0.02)
        phase = rng.uniform(0, 2 * math.pi)
        base_y = int(h * (wave_i + 1) / (n_waves + 1))
        color = (
            30 + wave_i * 20,
            80 + wave_i * 15,
            150 + wave_i * 10,
        )
        points = [(0, h)]
        for x in range(w + 1):
            y = base_y + int(amp * math.sin(freq * x + phase))
            points.append((x, y))
        points.append((w, h))
        draw.polygon(points, fill=color)
    return img


def _forest_silhouette(rng: random.Random) -> Image.Image:
    """Tree silhouettes against a dawn sky."""
    w, h = 1024, 768
    img = Image.new("RGB", (w, h))
    draw = ImageDraw.Draw(img)
    for y in range(h):
        t = y / h
        r = int(40 + 60 * t)
        g = int(60 + 40 * t)
        b = int(80 + 20 * (1 - t))
        draw.line([(0, y), (w, y)], fill=(r, g, b))
    # Trees
    for _ in range(rng.randint(15, 30)):
        tx = rng.randint(0, w)
        tree_h = rng.randint(100, 400)
        trunk_w = rng.randint(5, 12)
        base_y = h
        draw.rectangle(
            [tx - trunk_w, base_y - tree_h, tx + trunk_w, base_y],
            fill=(20, 25, 15),
        )
        for j in range(5):
            spread = int(trunk_w * (4 - j) * 2.5)
            tip_y = base_y - tree_h + j * tree_h // 8
            draw.polygon(
                [
                    (tx, tip_y),
                    (tx - spread, tip_y + tree_h // 5),
                    (tx + spread, tip_y + tree_h // 5),
                ],
                fill=(20, 30 + j * 3, 15),
            )
    return img


def _starfield(rng: random.Random) -> Image.Image:
    """Night sky with stars and a moon."""
    w, h = 1024, 768
    img = Image.new("RGB", (w, h), (5, 5, 20))
    draw = ImageDraw.Draw(img)
    for _ in range(rng.randint(200, 400)):
        x = rng.randint(0, w)
        y = rng.randint(0, h)
        brightness = rng.randint(100, 255)
        size = rng.choice([1, 1, 1, 2, 2, 3])
        draw.ellipse([x, y, x + size, y + size], fill=(brightness, brightness, brightness - 20))
    # Moon
    mx = rng.randint(w // 4, 3 * w // 4)
    my = rng.randint(50, h // 3)
    mr = rng.randint(30, 60)
    draw.ellipse([mx - mr, my - mr, mx + mr, my + mr], fill=(230, 230, 210))
    return img


def _abstract_circles(rng: random.Random) -> Image.Image:
    """Overlapping colorful circles on white."""
    w, h = 1024, 768
    img = Image.new("RGB", (w, h), (250, 250, 248))
    draw = ImageDraw.Draw(img)
    for _ in range(rng.randint(10, 25)):
        x = rng.randint(-100, w + 100)
        y = rng.randint(-100, h + 100)
        r = rng.randint(40, 200)
        color = (rng.randint(0, 255), rng.randint(0, 255), rng.randint(0, 255))
        draw.ellipse([x - r, y - r, x + r, y + r], outline=color, width=rng.randint(2, 6))
    return img


def _color_blocks(rng: random.Random) -> Image.Image:
    """Mondrian-style color blocks."""
    w, h = 1024, 768
    img = Image.new("RGB", (w, h), (250, 250, 245))
    draw = ImageDraw.Draw(img)
    palette = [
        (220, 40, 40),
        (40, 80, 180),
        (250, 200, 30),
        (250, 250, 245),
        (250, 250, 245),
        (30, 30, 30),
    ]
    xs = sorted([0] + [rng.randint(50, w - 50) for _ in range(rng.randint(3, 6))] + [w])
    ys = sorted([0] + [rng.randint(50, h - 50) for _ in range(rng.randint(3, 6))] + [h])
    for i in range(len(xs) - 1):
        for j in range(len(ys) - 1):
            color = rng.choice(palette)
            draw.rectangle([xs[i] + 3, ys[j] + 3, xs[i + 1] - 3, ys[j + 1] - 3], fill=color)
    # Black grid lines
    for x in xs[1:-1]:
        draw.rectangle([x - 3, 0, x + 3, h], fill=(30, 30, 30))
    for y in ys[1:-1]:
        draw.rectangle([0, y - 3, w, y + 3], fill=(30, 30, 30))
    return img


def _radial_burst(rng: random.Random) -> Image.Image:
    """Radial color burst from center."""
    w, h = 1024, 768
    img = Image.new("RGB", (w, h), (20, 20, 30))
    draw = ImageDraw.Draw(img)
    cx, cy = w // 2, h // 2
    n_rays = rng.randint(24, 48)
    for i in range(n_rays):
        angle = 2 * math.pi * i / n_rays
        length = rng.randint(300, 550)
        end_x = cx + int(length * math.cos(angle))
        end_y = cy + int(length * math.sin(angle))
        hue = int(360 * i / n_rays)
        r = int(128 + 127 * math.sin(math.radians(hue)))
        g = int(128 + 127 * math.sin(math.radians(hue + 120)))
        b = int(128 + 127 * math.sin(math.radians(hue + 240)))
        draw.line([(cx, cy), (end_x, end_y)], fill=(r, g, b), width=rng.randint(3, 8))
    return img


def _stripe_pattern(rng: random.Random) -> Image.Image:
    """Diagonal stripes with varied colors."""
    w, h = 1024, 768
    img = Image.new("RGB", (w, h))
    draw = ImageDraw.Draw(img)
    stripe_w = rng.randint(20, 50)
    n_stripes = (w + h) // stripe_w + 1
    for i in range(n_stripes):
        hue = int(360 * i / n_stripes)
        r = int(128 + 100 * math.sin(math.radians(hue)))
        g = int(128 + 100 * math.sin(math.radians(hue + 120)))
        b = int(128 + 100 * math.sin(math.radians(hue + 240)))
        x_start = i * stripe_w - h
        points = [
            (x_start, 0),
            (x_start + stripe_w, 0),
            (x_start + stripe_w + h, h),
            (x_start + h, h),
        ]
        draw.polygon(points, fill=(r, g, b))
    return img
