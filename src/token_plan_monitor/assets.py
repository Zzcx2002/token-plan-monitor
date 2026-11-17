from __future__ import annotations

import math

from PIL import Image, ImageDraw


STATUS_COLORS = {
    "ok": "#34C759",
    "warning": "#FF9F0A",
    "error": "#FF3B30",
    "unconfigured": "#8E8E93",
}


def make_brand_icon(size: int = 256) -> Image.Image:
    """Draw a Token monogram inside an open, recurring-quota ring."""
    scale = 4
    side = max(16, size) * scale
    image = Image.new("RGBA", (side, side), (0, 0, 0, 0))
    draw = ImageDraw.Draw(image)
    margin = round(side * 0.04)
    radius = round(side * 0.23)
    top_color = (85, 178, 255)
    bottom_color = (0, 122, 255)
    for y in range(margin, side - margin):
        fraction = (y - margin) / max(1, side - 2 * margin)
        color = tuple(
            round(start + (end - start) * fraction)
            for start, end in zip(top_color, bottom_color)
        ) + (255,)
        draw.line((margin, y, side - margin, y), fill=color, width=1)
    mask = Image.new("L", (side, side), 0)
    ImageDraw.Draw(mask).rounded_rectangle(
        (margin, margin, side - margin, side - margin), radius=radius, fill=255
    )
    image.putalpha(mask)

    draw = ImageDraw.Draw(image)
    foreground = (255, 255, 255, 255)
    stroke = round(side * 0.075)

    def rounded_line(start: tuple[float, float], end: tuple[float, float]) -> None:
        draw.line((*start, *end), fill=foreground, width=stroke)
        cap_radius = stroke / 2
        for x, y in (start, end):
            draw.ellipse(
                (x - cap_radius, y - cap_radius, x + cap_radius, y + cap_radius),
                fill=foreground,
            )

    center_x, center_y = side * 0.50, side * 0.48
    ring_radius = side * 0.25
    # Pillow grows arc strokes inward, so expand the box to center the stroke.
    ring_outer_radius = ring_radius + stroke / 2
    ring_box = (
        center_x - ring_outer_radius,
        center_y - ring_outer_radius,
        center_x + ring_outer_radius,
        center_y + ring_outer_radius,
    )
    # The upper-right opening suggests renewal without colliding with tray badges.
    start_angle, end_angle = 340, 290
    draw.arc(
        ring_box,
        start=start_angle,
        end=end_angle,
        fill=foreground,
        width=stroke,
    )
    for angle in (start_angle, end_angle):
        radians = math.radians(angle)
        point = (
            center_x + ring_radius * math.cos(radians),
            center_y + ring_radius * math.sin(radians),
        )
        cap_radius = stroke / 2
        draw.ellipse(
            (
                point[0] - cap_radius,
                point[1] - cap_radius,
                point[0] + cap_radius,
                point[1] + cap_radius,
            ),
            fill=foreground,
        )

    crossbar_y = side * 0.39
    rounded_line((side * 0.38, crossbar_y), (side * 0.62, crossbar_y))
    rounded_line((side * 0.50, crossbar_y), (side * 0.50, side * 0.63))
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
