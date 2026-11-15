"""Generate the M2-05 showcase title cards and the 1000x750 cover image.

Everything here is composed from the REAL screenshots recorded by
``scripts/record_showcase.mjs`` (web/public/showcase/*.png) plus text. No
screenshot is drawn by hand and no placeholder stands in for a real frame.

Outputs (written next to the screenshots):
  web/public/showcase/cover-1000x750.png   -- the repository cover image
  docs/assets/showcase-raw/title-card.png  -- 1440x900 video intro card
  docs/assets/showcase-raw/end-card.png    -- 1440x900 video outro card

Run:
    .venv/Scripts/python.exe scripts/make_showcase_graphics.py
"""

from __future__ import annotations

from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

REPO = Path(__file__).resolve().parents[1]
SHOWCASE = REPO / "web" / "public" / "showcase"
RAW = REPO / "docs" / "assets" / "showcase-raw"

W, H = 1440, 900
COVER_W, COVER_H = 1000, 750

#: The live/fake disclosure every artefact carries (M2-05 requirement).
DISCLOSURE = (
    "Live dashboard UI - scripted fake model provider - disposable demo data - no external AI calls"
)

FONT_CANDIDATES = [
    "C:/Windows/Fonts/segoeui.ttf",
    "C:/Windows/Fonts/arial.ttf",
    "C:/Windows/Fonts/tahoma.ttf",
]


def font(size: int, bold: bool = False):
    names = (
        ["C:/Windows/Fonts/segoeuib.ttf", "C:/Windows/Fonts/arialbd.ttf"]
        if bold
        else FONT_CANDIDATES
    )
    for name in names:
        try:
            return ImageFont.truetype(name, size)
        except OSError:
            continue
    return ImageFont.load_default()


def paste_fit(base: Image.Image, shot: Image.Image, box: tuple[int, int, int, int]) -> None:
    """Fit ``shot`` inside ``box`` (x, y, w, h), preserving aspect, and paste it."""
    x, y, w, h = box
    scale = min(w / shot.width, h / shot.height)
    size = (int(shot.width * scale), int(shot.height * scale))
    resized = shot.resize(size, Image.LANCZOS)
    base.paste(resized, (x + (w - size[0]) // 2, y + (h - size[1]) // 2))


def rounded_frame(img: Image.Image, color: tuple[int, int, int]) -> Image.Image:
    """Add a 2px rounded border so screenshots read as panels on the cover."""
    framed = Image.new("RGB", (img.width + 8, img.height + 8), color)
    framed.paste(img, (4, 4))
    return framed


def title_card() -> Image.Image:
    card = Image.new("RGB", (W, H), (15, 18, 25))
    d = ImageDraw.Draw(card)
    title = font(88, bold=True)
    sub = font(34)
    small = font(24)
    d.text((W // 2, 300), "OpsPilot", font=title, fill=(245, 247, 250), anchor="mm")
    d.text(
        (W // 2, 400),
        "An AI operations agent that investigates, cites its sources,",
        font=sub,
        fill=(190, 198, 210),
        anchor="mm",
    )
    d.text(
        (W // 2, 448),
        "gates its own risky actions, and executes with an audit trail.",
        font=sub,
        fill=(190, 198, 210),
        anchor="mm",
    )
    d.text(
        (W // 2, 560),
        "Demo path A - a duplicate charge, a human approval, a refund executed exactly once",
        font=small,
        fill=(140, 200, 160),
        anchor="mm",
    )
    d.text((W // 2, 640), DISCLOSURE, font=small, fill=(120, 128, 142), anchor="mm")
    return card


def end_card() -> Image.Image:
    card = Image.new("RGB", (W, H), (15, 18, 25))
    d = ImageDraw.Draw(card)
    head = font(52, bold=True)
    body = font(30)
    small = font(24)
    d.text((W // 2, 280), "Run it yourself", font=head, fill=(245, 247, 250), anchor="mm")
    d.text(
        (W // 2, 380),
        "python scripts/demo_paths.py up      # disposable stack, seeded fixtures",
        font=body,
        fill=(160, 210, 175),
        anchor="mm",
    )
    d.text(
        (W // 2, 440),
        "open http://localhost:3101/tickets  # walk paths A / B / C",
        font=body,
        fill=(160, 210, 175),
        anchor="mm",
    )
    d.text(
        (W // 2, 560),
        "The model is MODEL_PROVIDER=fake: a committed fixture replay. The refund is real code",
        font=small,
        fill=(150, 158, 172),
        anchor="mm",
    )
    d.text(
        (W // 2, 600),
        "over disposable demo data; no external AI service is called in this recording.",
        font=small,
        fill=(150, 158, 172),
        anchor="mm",
    )
    d.text((W // 2, 700), DISCLOSURE, font=small, fill=(110, 118, 132), anchor="mm")
    return card


def cover() -> Image.Image:
    cover = Image.new("RGB", (COVER_W, COVER_H), (15, 18, 25))
    d = ImageDraw.Draw(cover)
    d.text(
        (COVER_W // 2, 92), "OpsPilot", font=font(64, bold=True), fill=(245, 247, 250), anchor="mm"
    )
    d.text(
        (COVER_W // 2, 150),
        "A reliable AI operations agent for B2B billing support",
        font=font(26),
        fill=(190, 198, 210),
        anchor="mm",
    )
    # Two real screenshots, side by side: the parked gate and the approval card.
    parked = Image.open(SHOWCASE / "03-run-parked.png").convert("RGB")
    approval = Image.open(SHOWCASE / "04-approval-pending.png").convert("RGB")
    frame_color = (60, 66, 78)
    paste_fit(cover, rounded_frame(parked, frame_color), (30, 200, 460, 300))
    paste_fit(cover, rounded_frame(approval, frame_color), (510, 200, 460, 300))
    d.text(
        (COVER_W // 2, 560),
        "Investigate - cite - gate - execute - audit",
        font=font(30, bold=True),
        fill=(140, 200, 160),
        anchor="mm",
    )
    d.text(
        (COVER_W // 2, 640),
        "Real Next.js dashboard over api + worker + PostgreSQL/pgvector.",
        font=font(22),
        fill=(170, 178, 192),
        anchor="mm",
    )
    d.text(
        (COVER_W // 2, 676),
        "Refunds stop at a human approval gate; every step is on the record.",
        font=font(22),
        fill=(170, 178, 192),
        anchor="mm",
    )
    d.text((COVER_W // 2, 720), DISCLOSURE, font=font(17), fill=(120, 128, 142), anchor="mm")
    return cover


def main() -> None:
    RAW.mkdir(parents=True, exist_ok=True)
    title_card().save(RAW / "title-card.png")
    end_card().save(RAW / "end-card.png")
    cover().save(SHOWCASE / "cover-1000x750.png")
    print(f"wrote {RAW / 'title-card.png'}")
    print(f"wrote {RAW / 'end-card.png'}")
    print(f"wrote {SHOWCASE / 'cover-1000x750.png'}")


if __name__ == "__main__":
    main()
