from __future__ import annotations

from PIL import Image, ImageDraw


STATUS_COLORS = {
    "ok": "#34C759",
    "warning": "#FF9F0A",
    "error": "#FF3B30",
    "unconfigured": "#8E8E93",
}


def make_brand_icon(size: int = 256) -> Image.Image:
    """Draw a macOS-inspired squircle with three simple capacity bars."""
    scale = 4
    side = max(16, size) * scale
    image = Image.new("RGBA", (side, side), (0, 0, 0, 0))
    draw = ImageDraw.Draw(image)
    margin = round(side * 0.04)
    radius = round(side * 0.23)
    for y in range(margin, side - margin):
        fraction = (y - margin) / max(1, side - 2 * margin)
        color = (
            round(10 + 55 * fraction),
            round(132 - 45 * fraction),
            round(255 - 12 * fraction),
            255,
        )
        draw.line((margin, y, side - margin, y), fill=color, width=1)
    mask = Image.new("L", (side, side), 0)
    ImageDraw.Draw(mask).rounded_rectangle(
        (margin, margin, side - margin, side - margin), radius=radius, fill=255
    )
    image.putalpha(mask)

    draw = ImageDraw.Draw(image)
    bar_width = round(side * 0.115)
    gap = round(side * 0.07)
    total_width = bar_width * 3 + gap * 2
    left = (side - total_width) // 2
    baseline = round(side * 0.70)
    for index, height_fraction in enumerate((0.22, 0.36, 0.50)):
        x = left + index * (bar_width + gap)
        top = baseline - round(side * height_fraction)
        draw.rounded_rectangle(
            (x, top, x + bar_width, baseline),
            radius=bar_width // 2,
            fill=(255, 255, 255, 245),
        )
    return image.resize((size, size), Image.Resampling.LANCZOS)


def make_tray_icon(status: str = "ok") -> Image.Image:
    """Use the application identity with a shape-and-color status badge."""
    image = make_brand_icon(64)
    scale = 4
    badge_size = 22 * scale
    badge = Image.new("RGBA", (badge_size, badge_size), (0, 0, 0, 0))
    draw = ImageDraw.Draw(badge)
    color = STATUS_COLORS.get(status, STATUS_COLORS["unconfigured"])
    draw.ellipse((0, 0, badge_size - 1, badge_size - 1), fill="white")
    inset = 3 * scale
    draw.ellipse(
        (inset, inset, badge_size - inset - 1, badge_size - inset - 1), fill=color
    )
    if status == "error":
        width = 2 * scale
        draw.line((8 * scale, 8 * scale, 14 * scale, 14 * scale), fill="white", width=width)
        draw.line((14 * scale, 8 * scale, 8 * scale, 14 * scale), fill="white", width=width)
    elif status == "warning":
        draw.line(
            (11 * scale, 6 * scale, 11 * scale, 12 * scale),
            fill="white",
            width=2 * scale,
        )
        draw.ellipse(
            (10 * scale, 15 * scale, 12 * scale, 17 * scale), fill="white"
        )
    elif status == "unconfigured":
        draw.line(
            (7 * scale, 11 * scale, 15 * scale, 11 * scale),
            fill="white",
            width=2 * scale,
        )
    badge = badge.resize((22, 22), Image.Resampling.LANCZOS)
    image.alpha_composite(badge, (40, 40))
    return image
