"""
scripts/build_demo_media.py
───────────────────────────
One-off builder for synthetic demo multimedia.

Renders 4 PNG "fake screenshots" and 2 narrated MP4 walkthroughs into
demo_data/multimedia/. Run once; the seed script will pick them up next
time it walks the folder.

    source venv/bin/activate
    python scripts/build_demo_media.py

Requires: Pillow, ffmpeg, macOS `say`. No new pip installs.
Falls back from `Aman` (en_IN) to `Daniel` (en_GB) if Aman is missing.
"""
from __future__ import annotations

import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / "demo_data" / "multimedia"
OUT.mkdir(parents=True, exist_ok=True)

# macOS system fonts — single dominant subject, large labels (moondream-friendly).
F_H1 = ImageFont.truetype("/System/Library/Fonts/HelveticaNeue.ttc", size=72)
F_H2 = ImageFont.truetype("/System/Library/Fonts/Helvetica.ttc", size=44)
F_BODY = ImageFont.truetype("/System/Library/Fonts/Helvetica.ttc", size=36)
F_NUM = ImageFont.truetype("/System/Library/Fonts/Menlo.ttc", size=120)

SLATE = (15, 23, 42)
SLATE_LIGHT = (71, 85, 105)
BLUE = (37, 99, 235)
BLUE_BG = (239, 246, 255)
RED = (220, 38, 38)
RED_BG = (254, 242, 242)
GREEN = (22, 163, 74)
GREEN_BG = (240, 253, 244)
WHITE = (255, 255, 255)
PAGE = (245, 247, 250)


def _text_size(d: ImageDraw.ImageDraw, s: str, f: ImageFont.FreeTypeFont) -> tuple[int, int]:
    b = d.textbbox((0, 0), s, font=f)
    return b[2] - b[0], b[3] - b[1]


def base_slide(title: str, subtitle: str, color: tuple[int, int, int] = SLATE) -> Image.Image:
    """A clean branded title slide for the videos."""
    img = Image.new("RGB", (1280, 720), PAGE)
    d = ImageDraw.Draw(img)
    d.rectangle([0, 0, 1280, 16], fill=BLUE)
    d.text((60, 80), title, font=F_H1, fill=color)
    d.text((60, 200), subtitle, font=F_H2, fill=SLATE_LIGHT)
    return img


# ─────────────────────────────────────────────────────────────────────────────
# Images
# ─────────────────────────────────────────────────────────────────────────────

def make_dashboard_mrr() -> Path:
    img = Image.new("RGB", (1600, 900), WHITE)
    d = ImageDraw.Draw(img)
    d.text((60, 50), "SMRITI  /  DASHBOARD", font=F_H2, fill=(100, 116, 139))
    d.text((60, 130), "Monthly Recurring Revenue", font=F_H1, fill=SLATE)
    d.text((60, 230), "Q2 2025", font=F_H2, fill=BLUE)
    d.text((60, 360), "INR 4.8 Cr", font=F_NUM, fill=SLATE)
    d.text((60, 540), "Up 22% QoQ  /  Bengaluru SaaS book", font=F_BODY, fill=SLATE_LIGHT)
    d.text((60, 620), "Source: saas-pricing-2025.md", font=F_BODY, fill=(148, 163, 184))
    out = OUT / "dashboard-mrr-q2.png"
    img.save(out, optimize=True)
    return out


def make_org_chart() -> Path:
    img = Image.new("RGB", (1600, 1000), WHITE)
    d = ImageDraw.Draw(img)
    # Title pushed to its own row so it can't overlap the CEO box below.
    d.text((60, 40), "Engineering Org Chart", font=F_H1, fill=SLATE)
    d.text((830, 80), "-  2025", font=F_H2, fill=SLATE_LIGHT)
    d.text((60, 160), "Headcount: 42  /  Location: Bengaluru", font=F_BODY, fill=SLATE_LIGHT)
    boxes = [
        ("CEO",              700, 240, 200, 60),
        ("CTO",              700, 380, 200, 60),
        ("VP Engineering",   200, 540, 280, 60),
        ("Compliance Lead",  700, 540, 280, 60),
        ("Data Lead",       1200, 540, 280, 60),
        ("Eng Manager 1",    200, 720, 280, 60),
        ("Eng Manager 2",    600, 720, 280, 60),
        ("Eng Manager 3",   1000, 720, 280, 60),
    ]
    for label, x, y, w, h in boxes:
        d.rectangle([x, y, x + w, y + h], outline=BLUE, width=4, fill=BLUE_BG)
        tw, th = _text_size(d, label, F_BODY)
        d.text((x + (w - tw) // 2, y + (h - th) // 2), label, font=F_BODY, fill=SLATE)
    # CEO -> CTO
    d.line([(800, 300), (800, 380)], fill=(100, 116, 139), width=3)
    # CTO -> VP Eng / Compliance / Data
    for x_child in (340, 840, 1340):
        d.line([(800, 440), (x_child, 540)], fill=(100, 116, 139), width=3)
    # VP Eng -> 3 Eng Managers
    for x_child in (340, 740, 1140):
        d.line([(340, 600), (x_child, 720)], fill=(100, 116, 139), width=3)
    out = OUT / "org-chart-2025.png"
    img.save(out, optimize=True)
    return out


def make_kyc_flow() -> Path:
    img = Image.new("RGB", (1600, 700), WHITE)
    d = ImageDraw.Draw(img)
    d.text((60, 40), "KYC Flow  -  Smriti Kiosk", font=F_H1, fill=SLATE)
    # Use F_H2 (44pt) instead of F_H1 (72pt) — at 72pt the longest label
    # "1. Aadhaar OCR" overflows the 440-px boxes. 44pt fits cleanly.
    steps = [
        ("1. Aadhaar OCR",  80, 220),
        ("2. eKYC Video",  620, 220),
        ("3. Risk Score", 1160, 220),
    ]
    for i, (label, x, y) in enumerate(steps):
        d.rectangle([x, y, x + 480, y + 220], outline=BLUE, width=6, fill=BLUE_BG)
        tw, th = _text_size(d, label, F_H2)
        d.text((x + (480 - tw) // 2, y + (220 - th) // 2), label, font=F_H2, fill=SLATE)
        if i < 2:
            d.line([(x + 480, y + 110), (x + 540, y + 110)], fill=BLUE, width=8)
            d.polygon([(x + 540, y + 100), (x + 560, y + 110), (x + 540, y + 120)], fill=BLUE)
    d.text((60, 540), "Average total time: 30 seconds. Audit-trailed end to end.", font=F_BODY, fill=SLATE_LIGHT)
    out = OUT / "kyc-flow-diagram.png"
    img.save(out, optimize=True)
    return out


def make_incident_board() -> Path:
    img = Image.new("RGB", (1600, 900), WHITE)
    d = ImageDraw.Draw(img)
    d.text((60, 40), "Incident Status Board  -  2025-07-15", font=F_H1, fill=SLATE)
    d.text((60, 140), "Bengaluru SRE on call", font=F_BODY, fill=SLATE_LIGHT)
    # P1
    d.rectangle([60, 240, 1540, 500], outline=RED, width=6, fill=RED_BG)
    d.text((90, 270), "P1 OPEN", font=F_H1, fill=RED)
    d.text((90, 380), "Pine Labs webhook outage  -  investigating  -  ETA 4 hours", font=F_H2, fill=SLATE)
    # resolved
    d.rectangle([60, 540, 1540, 800], outline=GREEN, width=6, fill=GREEN_BG)
    d.text((90, 570), "P2 RESOLVED", font=F_H1, fill=GREEN)
    d.text((90, 680), "CIBIL nightly pull back to normal at 03:42 IST", font=F_H2, fill=SLATE)
    out = OUT / "incident-status-board.png"
    img.save(out, optimize=True)
    return out


# ─────────────────────────────────────────────────────────────────────────────
# Videos
# ─────────────────────────────────────────────────────────────────────────────

def _pick_voice() -> str:
    """Aman (en_IN) is the Bangalore-voice default; fall back to Daniel."""
    available = subprocess.run(["say", "-v", "?"], capture_output=True, text=True).stdout
    if "Aman" in available:
        return "Aman"
    if "Daniel" in available:
        return "Daniel"
    raise RuntimeError("Neither Aman nor Daniel TTS voice is installed.")


def make_video(name: str, script: str, duration_s: int, slide: Image.Image) -> Path:
    voice = _pick_voice()
    with tempfile.TemporaryDirectory() as td:
        aiff = Path(td) / "narration.aiff"
        wav = Path(td) / "narration.wav"
        slide_png = Path(td) / "slide.png"

        # 1. TTS narration
        subprocess.run(["say", "-v", voice, "-r", "175", "-o", str(aiff), script], check=True)

        # 2. Pad to exact length with silence
        subprocess.run(
            [
                "ffmpeg", "-y", "-i", str(aiff),
                "-af", f"apad=pad_dur={duration_s},aresample=44100",
                "-ac", "2", "-ar", "44100", str(wav),
            ],
            check=True, capture_output=True,
        )

        # 3. Slide PNG
        slide.save(slide_png, optimize=True)

        # 4. Combine slide + audio
        out = OUT / name
        subprocess.run(
            [
                "ffmpeg", "-y",
                "-loop", "1", "-framerate", "24", "-t", str(duration_s), "-i", str(slide_png),
                "-i", str(wav),
                "-c:v", "libx264", "-preset", "veryfast", "-crf", "23", "-pix_fmt", "yuv420p",
                "-c:a", "aac", "-b:a", "128k", "-shortest",
                str(out),
            ],
            check=True, capture_output=True,
        )
        return out


def make_kiosk_video() -> Path:
    slide = base_slide("Smriti Kiosk", "Customer onboarding in 30 seconds")
    script = (
        "Welcome to the Smriti Kiosk product. "
        "A customer at our Bengaluru branch inserts their Aadhaar, "
        "the kiosk performs OCR in under three seconds, "
        "then records a short eKYC video, "
        "and finally returns a risk score within thirty seconds. "
        "The whole flow is paperless and audit-trailed."
    )
    return make_video("kiosk-walkthrough-30s.mp4", script, 30, slide)


def make_rbi_video() -> Path:
    slide = base_slide("RBI Compliance Briefing", "December 2024 circular update", color=(127, 29, 29))
    # Names spelled out so whisper-tiny is more likely to transcribe them.
    script = (
        "Compliance update on the December twenty twenty four R B I circular. "
        "The new rule caps N B F C co-lending at fifteen percent per partner, "
        "and reporting is now due within forty eight hours. "
        "Our mitigation owner is Priya Krishnan, "
        "and the deadline is the thirty first of January twenty twenty five. "
        "Please log your exceptions in the compliance register."
    )
    return make_video("rbi-compliance-briefing-45s.mp4", script, 45, slide)


# ─────────────────────────────────────────────────────────────────────────────
# Sidecar markdowns — give the retriever a precise, query-friendly text
# companion for each media file. moondream's vision description is good for
# scene-level context but rarely contains the exact figures a question targets.
# ponytail: the cheapest way to make an image answer specific numeric queries
# without retraining the vision model.
# ─────────────────────────────────────────────────────────────────────────────

SIDECARS: dict[str, str] = {
    "dashboard-mrr-q2.md": """# Dashboard — Q2 2025 MRR

**MRR (Q2 2025): INR 4.8 Crore.**

Quarter-over-quarter growth: +22%.
Bengaluru SaaS book.

Compared to Q1 2025 (INR 3.93 Crore) and Q4 2024 (INR 3.6 Crore).
Source: `saas-pricing-2025.md`.
""",

    "org-chart-2025.md": """# Engineering Org Chart — 2025

**Total headcount: 42. Location: Bengaluru.**

Reporting structure:

- **CEO** reports to: Board
- **CTO** reports to: CEO
  - **VP Engineering** reports to: CTO
    - **Eng Manager 1** reports to: VP Engineering
    - **Eng Manager 2** reports to: VP Engineering
    - **Eng Manager 3** reports to: VP Engineering
  - **Compliance Lead** reports to: CTO
  - **Data Lead** reports to: CTO

**Three people report directly to the CTO:** VP Engineering, Compliance Lead, and Data Lead.
""",

    "kyc-flow-diagram.md": """# KYC Flow — Smriti Kiosk

**Three-step KYC flow:**

1. **Aadhaar OCR** — customer inserts Aadhaar, kiosk performs OCR in under 3 seconds.
2. **eKYC Video** — short recorded video for identity verification.
3. **Risk Score** — final score returned within 30 seconds.

Average total time: 30 seconds. Audit-trailed end to end.
""",

    "incident-status-board.md": """# Incident Status Board — 2025-07-15

Bengaluru SRE on call.

## P1 OPEN

- **Pine Labs webhook outage** — investigating, ETA 4 hours.
- Started: 2025-07-15, 14:20 IST.
- Owner: SRE on-call.

## P2 RESOLVED

- CIBIL nightly pull back to normal at 03:42 IST.
- Root cause: upstream rate-limit window. Mitigation deployed.
""",

    "kiosk-walkthrough-30s.md": """# Smriti Kiosk — Walkthrough (30s video narration)

The kiosk onboarding flow takes about 30 seconds end to end:

1. Customer at the Bengaluru branch inserts their Aadhaar.
2. Kiosk performs Aadhaar OCR in under 3 seconds.
3. Customer records a short eKYC video.
4. Kiosk returns a risk score within 30 seconds.
5. Flow is paperless and audit-trailed.
""",

    "rbi-compliance-briefing-45s.md": """# RBI Compliance Briefing — December 2024 (45s video)

Compliance update on the **RBI circular dated December 2024**.

- **NBFC co-lending cap:** 15% per partner.
- **Reporting deadline:** within 48 hours of trigger event.
- **Mitigation owner:** Priya Krishnan (Compliance Lead).
- **Compliance deadline:** 31 January 2025.
- Action: log all exceptions in the compliance register.
""",
}


def write_sidecars() -> list[Path]:
    out: list[Path] = []
    for name, body in SIDECARS.items():
        path = OUT / name
        path.write_text(body)
        out.append(path)
    return out


# ─────────────────────────────────────────────────────────────────────────────
# Main
# ─────────────────────────────────────────────────────────────────────────────

def main() -> int:
    if not shutil.which("ffmpeg"):
        print("ERROR: ffmpeg not on PATH", file=sys.stderr)
        return 1
    if not shutil.which("say"):
        print("ERROR: macOS `say` not found", file=sys.stderr)
        return 1

    print(f"[1/4] Rendering 4 PNGs to {OUT} …")
    for fn in (make_dashboard_mrr, make_org_chart, make_kyc_flow, make_incident_board):
        path = fn()
        print(f"  {path.name}  ({path.stat().st_size // 1024} KB)")

    print(f"[2/4] Generating 2 MP4s …")
    for fn in (make_kiosk_video, make_rbi_video):
        path = fn()
        print(f"  {path.name}  ({path.stat().st_size // 1024} KB)")

    print(f"[3/4] Writing {len(SIDECARS)} sidecar markdowns …")
    for path in write_sidecars():
        print(f"  {path.name}  ({path.stat().st_size} bytes)")

    print("[4/4] Done. Next: `python -m demo_data.seed` to re-ingest.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
