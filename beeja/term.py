"""Small terminal styling helpers (ANSI colours, badges, panels). No dependencies.

Colour is used only when writing to a terminal; set ``NO_COLOR=1`` to disable it or
``FORCE_COLOR=1`` to force it (e.g. under ``make``). Piped / redirected output stays plain text,
so JSON output, files and tests are unaffected.
"""

from __future__ import annotations

import os
import re
import shutil
import sys
import textwrap

_CODES = {
    "reset": "0", "bold": "1", "dim": "2", "italic": "3", "under": "4",
    "red": "31", "green": "32", "yellow": "33", "blue": "34", "magenta": "35", "cyan": "36", "gray": "90",
    "bred": "91", "bgreen": "92", "byellow": "93", "bblue": "94", "bmagenta": "95", "bcyan": "96",
}
_ANSI = re.compile(r"\x1b\[[0-9;]*m")

STATUS_STYLE = {
    "accepted": ("bgreen", "✔"), "decided": ("bgreen", "✔"),
    "rejected": ("bred", "✘"), "withdrawn": ("bmagenta", "↩"), "deferred": ("byellow", "⏸"),
    "superseded": ("bmagenta", "⇢"), "unresolved": ("gray", "?"), "proposed": ("gray", "•"),
    "partially_decided": ("byellow", "◐"),
}


def enabled(stream=None) -> bool:
    if os.environ.get("NO_COLOR"):
        return False
    if os.environ.get("FORCE_COLOR"):
        return True
    stream = stream or sys.stdout
    return hasattr(stream, "isatty") and stream.isatty()


class Style:
    def __init__(self, color: bool) -> None:
        self.color = color

    def __call__(self, text: str, *styles: str) -> str:
        if not self.color or not styles:
            return text
        return "\x1b[" + ";".join(_CODES[s] for s in styles) + "m" + text + "\x1b[0m"

    def status(self, status: str) -> str:
        colour, icon = STATUS_STYLE.get(status, ("gray", "•"))
        return self(f"{icon} {status.replace('_', ' ')}", colour, "bold")

    def header(self, title: str, subtitle: str = "") -> str:
        w = width()
        line = self("━" * w, "cyan")
        sub = "\n" + self(subtitle, "dim") if subtitle else ""
        return f"\n{line}\n{self(title, 'bold', 'bcyan')}{sub}\n{line}"

    def section(self, title: str) -> str:
        return "\n" + self("▌ ", "cyan") + self(title, "bold")

    def kv(self, key: str, value: str, pad: int = 22) -> str:
        return "  " + self(key.ljust(pad), "dim") + value

    def quote(self, text: str, source: str = "", indent: int = 6) -> str:
        w = max(40, width() - indent - 4)
        body = textwrap.fill(" ".join(text.split()), w)
        bar = " " * indent + self("│ ", "gray")
        out = "\n".join(bar + self(ln, "italic") for ln in body.splitlines())
        if source:
            out += "\n" + " " * indent + self("  — " + source, "dim")
        return out

    def wrap(self, text: str, indent: int = 4) -> str:
        w = max(40, width() - indent)
        return textwrap.fill(" ".join(text.split()), w, initial_indent=" " * indent, subsequent_indent=" " * indent)

    def meter(self, value: float | None, label: str = "", size: int = 20) -> str:
        if value is None:
            return self("n/a", "dim")
        filled = round(value * size)
        colour = "bgreen" if value >= 0.75 else "byellow" if value >= 0.5 else "bred"
        return self("█" * filled, colour) + self("░" * (size - filled), "gray") + f"  {self(f'{value:.3f}', 'bold')} {label}"


def width() -> int:
    return max(60, min(100, shutil.get_terminal_size((88, 24)).columns))


def plain(text: str) -> str:
    return _ANSI.sub("", text)


def style(stream=None) -> Style:
    return Style(enabled(stream))
