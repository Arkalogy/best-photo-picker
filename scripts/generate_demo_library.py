#!/usr/bin/env python3
"""Generate a ~3700-photo demo library for Best Photo Picker.

Downloads real photos from Pexels API (baby/family heavy) and generates
synthetic images (screenshots, documents, panoramas, near-duplicates).

Usage:
    export PEXELS_API_KEY="your-key"
    python scripts/generate_demo_library.py

    # Or pass the key directly:
    python scripts/generate_demo_library.py --api-key YOUR_KEY

    # Custom output directory:
    python scripts/generate_demo_library.py --output ~/Pictures/my_demo

Get a free Pexels API key at: https://www.pexels.com/api/
"""

import argparse
import concurrent.futures
import os
import random
import sys
import time
from datetime import datetime, timedelta
from pathlib import Path

try:
    import requests
except ImportError:
    sys.exit("Missing 'requests'. Run: pip install requests")

try:
    from PIL import Image, ImageDraw, ImageEnhance, ImageFilter
except ImportError:
    sys.exit("Missing 'Pillow'. Run: pip install Pillow")

try:
    import piexif
except ImportError:
    piexif = None


# ─── Constants ──────────────────────────────────────────────────────────────

DEFAULT_OUTPUT = Path.home() / "Pictures" / "BestPhotoPicker_demo_source"
PEXELS_API = "https://api.pexels.com/v1"
PEXELS_VIDEO_API = "https://api.pexels.com/videos"
PER_PAGE = 80  # Pexels max per page
MAX_WORKERS = 10
API_DELAY = 0.05  # seconds between API search calls

# Camera models for EXIF variety
CAMERAS = [
    ("Apple", "iPhone 14 Pro"),
    ("Apple", "iPhone 13"),
    ("Apple", "iPhone 15 Pro Max"),
    ("Apple", "iPhone 12"),
    ("Samsung", "Galaxy S23 Ultra"),
    ("Samsung", "Galaxy S22"),
    ("Google", "Pixel 7 Pro"),
    ("Canon", "EOS R5"),
    ("Sony", "ILCE-7M4"),
    ("Nikon", "Z 6III"),
    ("Fujifilm", "X-T5"),
]

# GPS coordinates for variety (city, lat, lon)
GPS_LOCATIONS = [
    (40.7128, -74.0060),  # New York
    (34.0522, -118.2437),  # Los Angeles
    (51.5074, -0.1278),  # London
    (48.8566, 2.3522),  # Paris
    (35.6762, 139.6503),  # Tokyo
    (37.7749, -122.4194),  # San Francisco
    (41.8781, -87.6298),  # Chicago
    (52.5200, 13.4050),  # Berlin
    (-33.8688, 151.2093),  # Sydney
    (43.6532, -79.3832),  # Toronto
    (25.2048, 55.2708),  # Dubai
    (1.3521, 103.8198),  # Singapore
    (39.9042, 116.4074),  # Beijing
    (45.4642, 9.1900),  # Milan
    (59.3293, 18.0686),  # Stockholm
    (37.9838, 23.7275),  # Athens
    (55.6761, 12.5683),  # Copenhagen
    (47.3769, 8.5417),  # Zurich
]

# ─── Category definitions ───────────────────────────────────────────────────
# Each: queries to search, target count, batch folder name, year range for EXIF

CATEGORIES = [
    # ── Baby / Family (~2,400 = 65%) ──────────────────────────
    {
        "name": "Baby Portraits",
        "queries": ["baby portrait", "baby face close up", "cute baby"],
        "count": 350,
        "batch": "Baby_Album_2024",
        "years": (2024, 2024),
    },
    {
        "name": "Newborn",
        "queries": ["newborn baby", "newborn hospital", "tiny baby hands"],
        "count": 280,
        "batch": "Newborn_2025",
        "years": (2025, 2025),
    },
    {
        "name": "Baby Smiling",
        "queries": ["baby smiling", "baby laughing", "happy baby"],
        "count": 250,
        "batch": "Baby_Album_2024",
        "years": (2024, 2024),
    },
    {
        "name": "Baby Crawling",
        "queries": ["baby crawling", "baby first steps", "baby walking"],
        "count": 150,
        "batch": "Baby_Album_2023",
        "years": (2023, 2023),
    },
    {
        "name": "Mother & Baby",
        "queries": ["mother holding baby", "mom and baby", "mother breastfeeding"],
        "count": 280,
        "batch": "Family_Moments_2024",
        "years": (2024, 2024),
    },
    {
        "name": "Father & Baby",
        "queries": ["father holding baby", "dad and baby", "father daughter"],
        "count": 200,
        "batch": "Family_Moments_2023",
        "years": (2023, 2023),
    },
    {
        "name": "Family Group",
        "queries": [
            "happy family",
            "family outdoors",
            "family portrait photography",
        ],
        "count": 300,
        "batch": "Family_Moments_2022",
        "years": (2022, 2022),
    },
    {
        "name": "Toddler",
        "queries": ["toddler playing", "toddler park", "toddler toys"],
        "count": 200,
        "batch": "Toddler_Adventures",
        "years": (2023, 2024),
    },
    {
        "name": "Children",
        "queries": ["children playing", "kids outdoor", "siblings"],
        "count": 180,
        "batch": "Kids_Growing_Up",
        "years": (2021, 2023),
    },
    {
        "name": "Pregnancy",
        "queries": ["pregnancy", "maternity photo", "pregnant woman"],
        "count": 100,
        "batch": "Newborn_2025",
        "years": (2024, 2025),
    },
    {
        "name": "Baby Sleeping",
        "queries": ["baby sleeping", "sleeping infant", "peaceful baby"],
        "count": 120,
        "batch": "Baby_Album_2023",
        "years": (2023, 2023),
    },
    # ── Non-family (~780) ─────────────────────────────────────
    {
        "name": "Landscape",
        "queries": ["mountain landscape", "scenic nature", "valley panoramic"],
        "count": 120,
        "batch": "Outdoor_Adventures",
        "years": (2021, 2023),
    },
    {
        "name": "Beach & Sunset",
        "queries": ["beach sunset", "ocean waves", "tropical beach"],
        "count": 100,
        "batch": "Vacation_2022",
        "years": (2022, 2022),
    },
    {
        "name": "City",
        "queries": ["city skyline", "architecture modern", "street urban"],
        "count": 100,
        "batch": "City_Life",
        "years": (2021, 2024),
    },
    {
        "name": "Food",
        "queries": ["food photography", "cooking kitchen", "restaurant plating"],
        "count": 80,
        "batch": "Food_and_Cooking",
        "years": (2022, 2024),
    },
    {
        "name": "Dogs",
        "queries": ["dog portrait", "puppy cute", "dog playing"],
        "count": 80,
        "batch": "Pets",
        "years": (2022, 2024),
    },
    {
        "name": "Cats",
        "queries": ["cat portrait", "kitten cute", "cat sleeping"],
        "count": 70,
        "batch": "Pets",
        "years": (2022, 2024),
    },
    {
        "name": "Flowers",
        "queries": ["flower garden", "spring flowers", "rose bouquet"],
        "count": 60,
        "batch": "Outdoor_Adventures",
        "years": (2022, 2023),
    },
    {
        "name": "People",
        "queries": ["portrait photography", "friends group", "couple"],
        "count": 80,
        "batch": "Holidays_2023",
        "years": (2023, 2023),
    },
    {
        "name": "Celebration",
        "queries": ["birthday party", "celebration", "christmas family"],
        "count": 80,
        "batch": "Holidays_2022",
        "years": (2022, 2022),
    },
]

# Summary:
#   Pexels downloads: ~3,180  (family ~2,410 = 65%)
#   Synthetic:        ~550    (screenshots 200, docs 100, dupes 200, panoramas 50)
#   Videos:           ~20
#   Grand total:      ~3,750


# ─── Pexels API Client ─────────────────────────────────────────────────────


class PexelsClient:
    """Thin wrapper around the Pexels Search API with dedup."""

    def __init__(self, api_key: str):
        self.session = requests.Session()
        self.session.headers["Authorization"] = api_key
        self.seen_ids: set[int] = set()

    def search_photos(self, query: str, count: int) -> list[dict]:
        """Return up to *count* unique photo dicts from Pexels."""
        results: list[dict] = []
        page = 1
        while len(results) < count:
            resp = self.session.get(
                f"{PEXELS_API}/search",
                params={"query": query, "per_page": PER_PAGE, "page": page},
            )
            if resp.status_code == 429:
                print("    ⏳ Rate-limited, waiting 60 s ...")
                time.sleep(60)
                continue
            resp.raise_for_status()
            data = resp.json()
            photos = data.get("photos", [])
            if not photos:
                break
            for p in photos:
                if p["id"] not in self.seen_ids and len(results) < count:
                    self.seen_ids.add(p["id"])
                    results.append(
                        {
                            "id": p["id"],
                            "url": p["src"].get("large2x") or p["src"]["large"],  # ~2500px
                        }
                    )
            page += 1
            if not data.get("next_page"):
                break
            time.sleep(API_DELAY)
        return results

    def search_videos(self, query: str, count: int) -> list[dict]:
        """Return up to *count* video dicts (SD quality) from Pexels."""
        results: list[dict] = []
        page = 1
        while len(results) < count:
            resp = self.session.get(
                f"{PEXELS_VIDEO_API}/search",
                params={"query": query, "per_page": min(count, PER_PAGE), "page": page},
            )
            if resp.status_code == 429:
                time.sleep(60)
                continue
            resp.raise_for_status()
            data = resp.json()
            videos = data.get("videos", [])
            if not videos:
                break
            for v in videos:
                if v["id"] not in self.seen_ids and len(results) < count:
                    self.seen_ids.add(v["id"])
                    files = v.get("video_files", [])
                    sd = next((f for f in files if f.get("quality") == "sd"), None)
                    if not sd:
                        sd = files[0] if files else None
                    if sd:
                        results.append({"id": v["id"], "url": sd["link"]})
            page += 1
            time.sleep(API_DELAY)
        return results


# ─── Download helper ────────────────────────────────────────────────────────


def _download_one(url: str, dest: Path, session: requests.Session) -> bool:
    """Download a single URL to *dest*. Returns True on success."""
    if dest.exists() and dest.stat().st_size > 0:
        return True  # resume support
    try:
        resp = session.get(url, timeout=120)
        resp.raise_for_status()
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_bytes(resp.content)
        return True
    except Exception as exc:
        print(f"    ✗ {dest.name}: {exc}")
        return False


# ─── EXIF helpers ───────────────────────────────────────────────────────────


def _random_date(year_start: int, year_end: int) -> datetime:
    start = datetime(year_start, 1, 1)
    end = datetime(year_end, 12, 31, 23, 59, 59)
    delta = int((end - start).total_seconds())
    return start + timedelta(seconds=random.randint(0, delta))


def _deg_to_dms_rational(val: float):
    """Convert decimal degrees to EXIF-style ((d,1),(m,1),(s*100,100))."""
    d = int(val)
    m_float = (val - d) * 60
    m = int(m_float)
    s = round((m_float - m) * 60 * 100)
    return ((d, 1), (m, 1), (s, 100))


def set_exif(
    path: Path,
    dt: datetime,
    gps: tuple[float, float] | None = None,
    camera: tuple[str, str] | None = None,
) -> None:
    """Write EXIF metadata (date, camera, GPS) into a JPEG file."""
    if piexif is None:
        return
    try:
        exif_dict = piexif.load(str(path))
    except Exception:
        exif_dict = {"0th": {}, "Exif": {}, "GPS": {}, "1st": {}}

    date_str = dt.strftime("%Y:%m:%d %H:%M:%S").encode()
    exif_dict["Exif"][piexif.ExifIFD.DateTimeOriginal] = date_str
    exif_dict["Exif"][piexif.ExifIFD.DateTimeDigitized] = date_str
    exif_dict["0th"][piexif.ImageIFD.DateTime] = date_str

    if camera:
        exif_dict["0th"][piexif.ImageIFD.Make] = camera[0].encode()
        exif_dict["0th"][piexif.ImageIFD.Model] = camera[1].encode()

    if gps:
        lat, lon = gps
        exif_dict["GPS"] = {
            piexif.GPSIFD.GPSLatitudeRef: b"N" if lat >= 0 else b"S",
            piexif.GPSIFD.GPSLatitude: _deg_to_dms_rational(abs(lat)),
            piexif.GPSIFD.GPSLongitudeRef: b"E" if lon >= 0 else b"W",
            piexif.GPSIFD.GPSLongitude: _deg_to_dms_rational(abs(lon)),
        }

    try:
        exif_bytes = piexif.dump(exif_dict)
        piexif.insert(exif_bytes, str(path))
    except Exception:
        pass  # some images resist EXIF insertion


# ─── Synthetic generators ──────────────────────────────────────────────────


def _generate_screenshot(dest: Path, index: int) -> None:
    """Create a fake screenshot (desktop or mobile)."""
    if index % 2 == 0:
        # Desktop screenshot (16:10)
        w, h = 1440, 900
        bg = (30, 30, 30)
        img = Image.new("RGB", (w, h), bg)
        draw = ImageDraw.Draw(img)
        # macOS-style menu bar
        draw.rectangle([0, 0, w, 28], fill=(50, 50, 50))
        for i, c in enumerate([(255, 95, 86), (255, 189, 46), (39, 201, 63)]):
            draw.ellipse([10 + i * 20, 8, 22 + i * 20, 20], fill=c)
        # Window
        win_x, win_y = 80 + index % 40, 50 + index % 30
        draw.rectangle([win_x, win_y, w - win_x, h - 40], fill=(255, 255, 255))
        draw.rectangle([win_x, win_y, w - win_x, win_y + 32], fill=(60, 60, 60))
        # Content lines
        for y in range(win_y + 50, h - 60, 18):
            lw = random.randint(200, w - win_x * 2 - 40)
            gray = random.randint(180, 220)
            draw.rectangle([win_x + 20, y, win_x + 20 + lw, y + 9], fill=(gray, gray, gray))
    else:
        # Mobile screenshot (9:19.5)
        w, h = 390, 844
        bg = (245, 245, 245)
        img = Image.new("RGB", (w, h), bg)
        draw = ImageDraw.Draw(img)
        # Status bar
        draw.rectangle([0, 0, w, 44], fill=(255, 255, 255))
        # Content cards
        for y in range(70, h - 100, random.randint(100, 140)):
            card_h = random.randint(70, 110)
            draw.rounded_rectangle(
                [16, y, w - 16, y + card_h],
                radius=12,
                fill=(255, 255, 255),
            )
            draw.rectangle([30, y + 14, w - 30, y + 24], fill=(180, 180, 180))
            draw.rectangle(
                [30, y + 32, random.randint(w // 3, w // 2), y + 42], fill=(200, 200, 200)
            )
            if card_h > 80:
                draw.rectangle([30, y + 50, w - 60, y + 58], fill=(220, 220, 220))

    dest.parent.mkdir(parents=True, exist_ok=True)
    img.save(str(dest), "JPEG", quality=90)


def _generate_document(dest: Path, index: int) -> None:
    """Create a fake scanned document."""
    w, h = 850, 1100
    bg = random.choice([(255, 255, 255), (248, 245, 240), (240, 240, 245)])
    img = Image.new("RGB", (w, h), bg)
    draw = ImageDraw.Draw(img)

    # Title
    draw.rectangle([80, 50, w - 80, 72], fill=(40, 40, 40))
    # Subtitle
    draw.rectangle([80, 90, w // 2 + 100, 105], fill=(100, 100, 100))
    # Body lines
    y = 140
    while y < h - 80:
        lw = random.randint(400, w - 160)
        gray = random.randint(60, 100)
        draw.rectangle([80, y, 80 + lw, y + 9], fill=(gray, gray, gray))
        y += 17
        if random.random() < 0.12:
            y += 14  # paragraph gap

    # Slight rotation for realism
    if random.random() < 0.3:
        img = img.rotate(random.uniform(-1.5, 1.5), fillcolor=bg, expand=False)

    dest.parent.mkdir(parents=True, exist_ok=True)
    img.save(str(dest), "JPEG", quality=85)


def _generate_panorama(dest: Path, source: Path | None = None) -> None:
    """Create a panoramic image (3:1 aspect ratio)."""
    if source and source.exists():
        try:
            img = Image.open(source)
            w, h = img.size
            new_h = w // 3
            if new_h < h:
                top = (h - new_h) // 2
                img = img.crop((0, top, w, top + new_h))
            img = img.resize((2400, 800), Image.LANCZOS)
            dest.parent.mkdir(parents=True, exist_ok=True)
            img.save(str(dest), "JPEG", quality=85)
            return
        except Exception:
            pass

    # Gradient fallback
    w, h = 2400, 800
    img = Image.new("RGB", (w, h))
    draw = ImageDraw.Draw(img)
    c1 = (random.randint(0, 100), random.randint(100, 200), random.randint(180, 255))
    c2 = (random.randint(200, 255), random.randint(100, 200), random.randint(0, 100))
    for x in range(w):
        t = x / w
        r = int(c1[0] + (c2[0] - c1[0]) * t)
        g = int(c1[1] + (c2[1] - c1[1]) * t)
        b = int(c1[2] + (c2[2] - c1[2]) * t)
        draw.line([(x, 0), (x, h)], fill=(r, g, b))
    dest.parent.mkdir(parents=True, exist_ok=True)
    img.save(str(dest), "JPEG", quality=85)


def _create_near_duplicate(source: Path, dest: Path) -> None:
    """Create a near-duplicate with a subtle transform."""
    try:
        img = Image.open(source)
        op = random.choice(["brightness", "crop", "quality", "blur", "rotate"])

        if op == "brightness":
            img = ImageEnhance.Brightness(img).enhance(random.uniform(0.85, 1.15))
        elif op == "crop":
            w, h = img.size
            m = int(min(w, h) * 0.03)
            img = img.crop((m, m, w - m, h - m))
        elif op == "blur":
            img = img.filter(ImageFilter.GaussianBlur(radius=0.8))
        elif op == "rotate":
            img = img.rotate(random.uniform(-2, 2), fillcolor=(0, 0, 0))
        # "quality" just re-saves at different JPEG quality

        dest.parent.mkdir(parents=True, exist_ok=True)
        q = random.randint(55, 75) if op == "quality" else 85
        img.save(str(dest), "JPEG", quality=q)
    except Exception as exc:
        print(f"    ✗ dup from {source.name}: {exc}")


# ─── Main builder ──────────────────────────────────────────────────────────


def build_demo_library(api_key: str, output_dir: Path) -> None:
    output_dir.mkdir(parents=True, exist_ok=True)
    client = PexelsClient(api_key)
    dl_session = requests.Session()

    total_pexels_target = sum(c["count"] for c in CATEGORIES)
    total_pexels_got = 0

    # ── Phase 1: Pexels photos ──────────────────────────────────
    print(f"\n{'=' * 60}")
    print(f" Phase 1: Download ~{total_pexels_target} photos from Pexels")
    print(f"{'=' * 60}\n")

    for cat in CATEGORIES:
        batch_dir = output_dir / cat["batch"]
        batch_dir.mkdir(parents=True, exist_ok=True)

        print(f"  [{cat['name']}] target={cat['count']}")
        all_photos: list[dict] = []
        for query in cat["queries"]:
            if len(all_photos) >= cat["count"]:
                break
            remaining = cat["count"] - len(all_photos)
            found = client.search_photos(query, remaining)
            all_photos.extend(found)
            print(f"    '{query}' → {len(found)} photos")

        # Download in parallel
        ok = 0
        with concurrent.futures.ThreadPoolExecutor(max_workers=MAX_WORKERS) as pool:
            futures = {}
            for photo in all_photos:
                dest = batch_dir / f"{cat['batch'].lower()}_{photo['id']}.jpg"
                fut = pool.submit(_download_one, photo["url"], dest, dl_session)
                futures[fut] = dest
            for fut in concurrent.futures.as_completed(futures):
                if fut.result():
                    ok += 1
        total_pexels_got += ok
        print(f"    ✓ {ok}/{len(all_photos)} downloaded\n")

    print(f"  Total from Pexels: {total_pexels_got}")

    # ── Phase 2: Screenshots ────────────────────────────────────
    n_ss = 200
    print(f"\n{'=' * 60}")
    print(f" Phase 2: Generate {n_ss} synthetic screenshots")
    print(f"{'=' * 60}\n")

    ss_dir = output_dir / "Screenshots"
    ss_dir.mkdir(parents=True, exist_ok=True)
    created = 0
    for i in range(n_ss):
        dest = ss_dir / f"screenshot_{i:04d}.jpg"
        if not dest.exists():
            _generate_screenshot(dest, i)
            created += 1
    print(f"  ✓ {created} new screenshots ({n_ss} total)\n")

    # ── Phase 3: Documents ──────────────────────────────────────
    n_doc = 100
    print(f"\n{'=' * 60}")
    print(f" Phase 3: Generate {n_doc} synthetic documents")
    print(f"{'=' * 60}\n")

    doc_dir = output_dir / "Documents"
    doc_dir.mkdir(parents=True, exist_ok=True)
    created = 0
    for i in range(n_doc):
        dest = doc_dir / f"document_{i:04d}.jpg"
        if not dest.exists():
            _generate_document(dest, i)
            created += 1
    print(f"  ✓ {created} new documents ({n_doc} total)\n")

    # ── Phase 4: Panoramas ──────────────────────────────────────
    n_pano = 50
    print(f"\n{'=' * 60}")
    print(f" Phase 4: Generate {n_pano} panoramic images")
    print(f"{'=' * 60}\n")

    pano_dir = output_dir / "Outdoor_Adventures"
    pano_dir.mkdir(parents=True, exist_ok=True)
    landscape_srcs = list(pano_dir.glob("outdoor_adventures_*.jpg"))
    random.shuffle(landscape_srcs)
    created = 0
    for i in range(n_pano):
        dest = pano_dir / f"panorama_{i:04d}.jpg"
        if not dest.exists():
            src = landscape_srcs[i] if i < len(landscape_srcs) else None
            _generate_panorama(dest, src)
            created += 1
    print(f"  ✓ {created} new panoramas ({n_pano} total)\n")

    # ── Phase 5: Near-duplicates ────────────────────────────────
    n_dup = 200
    print(f"\n{'=' * 60}")
    print(f" Phase 5: Generate {n_dup} near-duplicates")
    print(f"{'=' * 60}\n")

    all_jpgs = [
        p
        for p in output_dir.rglob("*.jpg")
        if "_dup" not in p.stem
        and "screenshot" not in p.stem
        and "document" not in p.stem
        and "panorama" not in p.stem
    ]
    if all_jpgs:
        dup_sources = (
            random.sample(all_jpgs, n_dup)
            if len(all_jpgs) >= n_dup
            else random.choices(all_jpgs, k=n_dup)
        )
        created = 0
        for src in dup_sources:
            dest = src.parent / f"{src.stem}_dup.jpg"
            if not dest.exists():
                _create_near_duplicate(src, dest)
                created += 1
        print(f"  ✓ {created} duplicates created\n")
    else:
        print("  ⚠ No source images for duplicates\n")

    # ── Phase 6: Videos ─────────────────────────────────────────
    n_vid = 20
    print(f"\n{'=' * 60}")
    print(f" Phase 6: Download {n_vid} short videos")
    print(f"{'=' * 60}\n")

    vid_dir = output_dir / "Family_Videos"
    vid_dir.mkdir(parents=True, exist_ok=True)

    all_vids: list[dict] = []
    for q in ["baby playing", "family fun", "toddler walking", "puppy playing"]:
        if len(all_vids) >= n_vid:
            break
        found = client.search_videos(q, n_vid - len(all_vids))
        all_vids.extend(found)
        print(f"    '{q}' → {len(found)} videos")

    ok = 0
    for v in all_vids[:n_vid]:
        dest = vid_dir / f"video_{v['id']}.mp4"
        if _download_one(v["url"], dest, dl_session):
            ok += 1
    print(f"  ✓ {ok} videos downloaded\n")

    # ── Phase 7: EXIF metadata ──────────────────────────────────
    print(f"\n{'=' * 60}")
    print(" Phase 7: Set EXIF metadata")
    print(f"{'=' * 60}\n")

    if piexif is None:
        print("  ⚠ piexif not installed — skipping EXIF. Run: pip install piexif\n")
    else:
        # Build batch → year-range mapping
        batch_years: dict[str, tuple[int, int]] = {}
        for cat in CATEGORIES:
            batch_years[cat["batch"]] = cat["years"]
        batch_years["Screenshots"] = (2023, 2026)
        batch_years["Documents"] = (2022, 2025)
        batch_years["Family_Videos"] = (2023, 2025)

        all_photos = list(output_dir.rglob("*.jpg"))
        print(f"  Processing {len(all_photos)} photos ...")

        for i, photo in enumerate(all_photos):
            batch = photo.parent.name
            years = batch_years.get(batch, (2020, 2025))

            # 5% of photos get dates in the last 30 days (for "Last 30 Days" album)
            if random.random() < 0.05:
                now = datetime(2026, 2, 27, 23, 59, 59)
                dt = now - timedelta(
                    days=random.randint(0, 29),
                    hours=random.randint(0, 23),
                    minutes=random.randint(0, 59),
                )
            else:
                dt = _random_date(*years)

            camera = random.choice(CAMERAS) if random.random() < 0.5 else None
            gps = random.choice(GPS_LOCATIONS) if random.random() < 0.3 else None
            set_exif(photo, dt, gps, camera)

            if (i + 1) % 500 == 0:
                print(f"    {i + 1}/{len(all_photos)} ...")

        print(f"  ✓ EXIF written on {len(all_photos)} photos\n")

    # ── Summary ─────────────────────────────────────────────────
    total_files = len(list(output_dir.rglob("*.jpg"))) + len(list(output_dir.rglob("*.mp4")))
    print(f"\n{'=' * 60}")
    print(f" DONE!  {total_files} files in {output_dir}")
    print(f"{'=' * 60}\n")

    print("Batch folders:")
    for d in sorted(output_dir.iterdir()):
        if d.is_dir():
            n = len(list(d.iterdir()))
            print(f"  {d.name:30s} {n:>5} files")

    print("\nTo import into Best Photo Picker:")
    print("  1. Open the app")
    print("  2. Use Import to add each batch folder from:")
    print(f"     {output_dir}")


# ─── CLI ────────────────────────────────────────────────────────────────────


def main():
    parser = argparse.ArgumentParser(
        description="Generate a ~3700-photo demo library for Best Photo Picker",
    )
    parser.add_argument(
        "--api-key",
        default=os.environ.get("PEXELS_API_KEY"),
        help="Pexels API key (or set PEXELS_API_KEY env var)",
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=DEFAULT_OUTPUT,
        help=f"Output directory (default: {DEFAULT_OUTPUT})",
    )
    args = parser.parse_args()

    if not args.api_key:
        print("Error: Pexels API key required.\n")
        print("  Get a free key at: https://www.pexels.com/api/")
        print("  Then either:")
        print("    export PEXELS_API_KEY='your-key'")
        print("    python scripts/generate_demo_library.py")
        print("  Or:")
        print("    python scripts/generate_demo_library.py --api-key YOUR_KEY")
        sys.exit(1)

    build_demo_library(args.api_key, args.output)


if __name__ == "__main__":
    main()
