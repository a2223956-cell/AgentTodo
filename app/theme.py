"""Dark widget palette + fonts. Colors only live here so the UI code stays clean."""

BG = "#151827"          # window behind panels (also the transparent key color substitute)
CARD = "#1E2233"        # panel body
CARD_ALT = "#262B40"    # rows / fields
CARD_HOVER = "#2E3450"
STROKE = "#343B55"
TEXT = "#E9ECF7"
TEXT_SUB = "#9AA3BC"
TEXT_DIM = "#6B7392"
ACCENT = "#6E8BFF"
ACCENT_SOFT = "#3A4370"
OK = "#4ADE80"
WARN = "#FBBF24"
DANGER = "#F87171"

# The transparent color key for rounded windows (a near-black not used in the palette).
TRANSPARENT = "#010203"

FONT_FAMILY = "Microsoft YaHei UI"


def font(size: int, bold: bool = False):
    return (FONT_FAMILY, size, "bold" if bold else "normal")


def rounded_rect(canvas, x1, y1, x2, y2, r, **kw):
    """Draw a rounded rectangle on a canvas; returns the item id."""
    points = [
        x1 + r, y1,
        x2 - r, y1,
        x2, y1, x2, y1 + r,
        x2, y2 - r,
        x2, y2, x2 - r, y2,
        x1 + r, y2,
        x1, y2, x1, y2 - r,
        x1, y1 + r,
        x1, y1, x1 + r, y1,
    ]
    return canvas.create_polygon(points, smooth=True, **kw)
