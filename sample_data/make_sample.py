"""Generates the sample invoice image shipped with this repo.

Kept as a script (rather than just committing the PNG blind) so the
sample can be regenerated or varied when testing extraction accuracy
against different layouts.

Needs Pillow, which is deliberately NOT in requirements.txt - it's only
used by this dev-time script and would bloat the runtime image for no
benefit. Install it just to regenerate:

    pip install pillow
    .venv\\Scripts\\python.exe sample_data\\make_sample.py
"""
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

OUT = Path(__file__).parent / "sample_invoice.png"
W, H = 1000, 1250


def _font(size, bold=False):
    """Windows-safe font lookup with a graceful fallback."""
    for name in (["arialbd.ttf", "segoeuib.ttf"] if bold else ["arial.ttf", "segoeui.ttf"]):
        try:
            return ImageFont.truetype(name, size)
        except OSError:
            continue
    return ImageFont.load_default(size)


def build() -> Path:
    img = Image.new("RGB", (W, H), "white")
    d = ImageDraw.Draw(img)

    f_title = _font(40, bold=True)
    f_h = _font(19, bold=True)
    f = _font(18)
    f_sm = _font(15)

    d.text((60, 55), "INVOICE", font=f_title, fill="#222222")

    d.text((60, 125), "Brightwood Supply Co.", font=f_h, fill="#111111")
    for i, line in enumerate([
        "148 Kingsley Avenue, Suite 4",
        "Portland, OR 97209",
        "billing@brightwoodsupply.com",
        "(503) 555-0142",
    ]):
        d.text((60, 158 + i * 26), line, font=f, fill="#333333")

    right = 600
    for i, (label, val) in enumerate([
        ("Invoice #:", "BW-2026-0871"),
        ("Invoice Date:", "August 14, 2026"),
        ("Due Date:", "September 13, 2026"),
        ("Terms:", "Net 30"),
    ]):
        d.text((right, 125 + i * 28), label, font=f_h, fill="#111111")
        d.text((right + 170, 125 + i * 28), val, font=f, fill="#333333")

    d.text((right, 265), "Bill To:", font=f_h, fill="#111111")
    for i, line in enumerate(["Marigold Design Studio", "902 SE Belmont St", "Portland, OR 97214"]):
        d.text((right, 295 + i * 25), line, font=f, fill="#333333")

    # line-item table
    top = 430
    d.rectangle([60, top, W - 60, top + 42], fill="#f0f0ee")
    for label, x in [("Description", 75), ("Qty", 600), ("Unit Price", 700), ("Amount", 860)]:
        d.text((x, top + 11), label, font=f_h, fill="#333333")

    rows = [
        ("Recycled kraft mailer boxes (12x9x4)", "250", "$1.86", "$465.00"),
        ("Custom logo stamp - 2 color", "1", "$139.00", "$139.00"),
        ("Biodegradable packing peanuts (14 cu ft)", "6", "$32.50", "$195.00"),
        ("Rush handling fee", "1", "$45.00", "$45.00"),
    ]
    y = top + 42
    for desc, qty, unit, amt in rows:
        d.line([60, y, W - 60, y], fill="#e4e4e0", width=1)
        d.text((75, y + 14), desc, font=f, fill="#222222")
        d.text((600, y + 14), qty, font=f, fill="#222222")
        d.text((700, y + 14), unit, font=f, fill="#222222")
        d.text((860, y + 14), amt, font=f, fill="#222222")
        y += 48
    d.line([60, y, W - 60, y], fill="#e4e4e0", width=1)

    # totals
    ty = y + 40
    for label, val, bold in [
        ("Subtotal", "$844.00", False),
        ("Shipping", "$38.75", False),
        ("Sales Tax (0%)", "$0.00", False),
    ]:
        d.text((690, ty), label, font=f, fill="#333333")
        d.text((880, ty), val, font=f, fill="#333333")
        ty += 32

    d.line([690, ty + 4, W - 60, ty + 4], fill="#333333", width=2)
    d.text((690, ty + 16), "Total Due", font=f_h, fill="#111111")
    d.text((880, ty + 16), "$882.75", font=f_h, fill="#111111")

    d.text((60, ty + 90),
           "Payment via ACH or check. Late payments accrue 1.5% monthly interest.",
           font=f_sm, fill="#777777")

    img.save(OUT)
    return OUT


if __name__ == "__main__":
    print(f"wrote {build()}")
