"""Hover tooltips: a small hint window shown when the mouse rests on a widget."""
from __future__ import annotations

import tkinter as tk

from . import theme as T


class Tooltip:
    """Attach a hover hint to any widget without disturbing its own bindings.

    Shows after `delay` ms of hovering, hides on leave, click, or when the
    widget goes away / its panel is withdrawn. Extra <Enter>/<Leave> handlers
    are registered with add="+", so existing bindings keep working.
    """

    def __init__(self, widget, text: str, delay: int = 350, above: bool = False):
        self.widget = widget
        self.text = text
        self.delay = delay
        self.above = above
        self._after: str | None = None
        self._tip: tk.Toplevel | None = None
        widget.bind("<Enter>", self._schedule, add="+")
        widget.bind("<Leave>", self.hide, add="+")
        widget.bind("<Button-1>", self.hide, add="+")
        widget.bind("<Button-3>", self.hide, add="+")
        widget.bind("<Destroy>", self.hide, add="+")

    # ------------------------------------------------------------- lifecycle
    def _schedule(self, _event=None):
        self._cancel()
        if self._tip is None:
            self._after = self.widget.after(self.delay, self._show)

    def _cancel(self):
        if self._after is not None:
            try:
                self.widget.after_cancel(self._after)
            except tk.TclError:
                pass
            self._after = None

    def hide(self, _event=None):
        self._cancel()
        tip, self._tip = self._tip, None
        if tip is not None:
            try:
                tip.destroy()
            except tk.TclError:
                pass

    def _alive(self) -> bool:
        try:
            return bool(self.widget.winfo_exists() and self.widget.winfo_viewable())
        except tk.TclError:
            return False

    # ------------------------------------------------------------- display
    def _show(self):
        self._after = None
        if self._tip is not None or not self.text or not self._alive():
            return
        tip = tk.Toplevel(self.widget)
        tip.wm_overrideredirect(True)
        try:
            tip.wm_attributes("-topmost", True)
        except tk.TclError:
            pass
        tip.configure(bg=T.TOOLTIP_BG)
        tk.Label(tip, text=self.text, font=T.font(8), fg=T.TEXT,
                 bg=T.TOOLTIP_BG, padx=8, pady=4, justify="left", anchor="w",
                 wraplength=280).pack()
        tip.update_idletasks()
        w, h = tip.winfo_reqwidth(), tip.winfo_reqheight()
        x = self.widget.winfo_rootx()
        y = self.widget.winfo_rooty()
        y = y - (h + 8) if self.above else y + self.widget.winfo_height() + 8
        sw, sh = tip.winfo_screenwidth(), tip.winfo_screenheight()
        x = max(4, min(x, sw - w - 4))
        y = max(4, min(y, sh - h - 4))
        tip.wm_geometry(f"+{x}+{y}")
        self._tip = tip
        self._watch()

    def _watch(self):
        """Drop the hint if the widget vanishes or its panel gets withdrawn."""
        if self._tip is None:
            return
        if not self._alive():
            self.hide()
            return
        self._after = self.widget.after(200, self._watch)
