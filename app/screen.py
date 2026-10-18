"""Screen geometry helpers: multi-monitor aware, Tk logical pixels."""
from __future__ import annotations

import ctypes
import sys

# win32 GetSystemMetrics codes for the virtual desktop (all monitors).
SM_XVIRTUALSCREEN = 76
SM_YVIRTUALSCREEN = 77
SM_CXVIRTUALSCREEN = 78
SM_CYVIRTUALSCREEN = 79
SM_CXSCREEN = 0   # primary width, used to derive the logical/physical scale
SM_CYSCREEN = 1


def _physical_virtual_screen() -> tuple[int, int, int, int, int, int] | None:
    """(vx, vy, vw, vh, primary_w, primary_h) in physical pixels, or None."""
    if sys.platform != "win32":
        return None
    try:
        sm = ctypes.windll.user32.GetSystemMetrics
        vx = sm(SM_XVIRTUALSCREEN)
        vy = sm(SM_YVIRTUALSCREEN)
        vw = sm(SM_CXVIRTUALSCREEN)
        vh = sm(SM_CYVIRTUALSCREEN)
        pw = sm(SM_CXSCREEN)
        ph = sm(SM_CYSCREEN)
        if vw > 0 and vh > 0 and pw > 0 and ph > 0:
            return vx, vy, vw, vh, pw, ph
    except Exception:
        pass
    return None


def virtual_screen(widget) -> tuple[int, int, int, int]:
    """(left, top, right, bottom) of the whole virtual desktop in Tk logical px.

    `winfo_screenwidth/height` only cover the primary monitor, so widgets
    clamped to them jump back to the primary screen when used on a second
    monitor. The win32 virtual-screen metrics cover every monitor; they are
    physical pixels, converted to Tk logical pixels via the primary scale.
    """
    phys = _physical_virtual_screen()
    try:
        lw = widget.winfo_screenwidth()
        lh = widget.winfo_screenheight()
    except Exception:
        lw = lh = 0
    if phys and lw and lh:
        vx, vy, vw, vh, pw, ph = phys
        sx = pw / lw
        sy = ph / lh
        return (round(vx / sx), round(vy / sy),
                round((vx + vw) / sx), round((vy + vh) / sy))
    try:  # Tk virtual-root metrics (X11 / fallback)
        x, y = widget.winfo_vrootx(), widget.winfo_vrooty()
        return (x, y, x + widget.winfo_vrootwidth(), y + widget.winfo_vrootheight())
    except Exception:
        return (0, 0, lw, lh)


def clamp_xy(x: int, y: int, w: int, h: int,
             bounds: tuple[int, int, int, int]) -> tuple[int, int]:
    """Keep a w×h window at (x, y) fully inside bounds; smaller screens pin to origin."""
    x1, y1, x2, y2 = bounds
    if w <= x2 - x1:
        x = min(max(x, x1), x2 - w)
    else:
        x = x1
    if h <= y2 - y1:
        y = min(max(y, y1), y2 - h)
    else:
        y = y1
    return x, y
