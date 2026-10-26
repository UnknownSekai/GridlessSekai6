from __future__ import annotations

import io
import os

CHART_EXTS = (".sus", ".usc")
CHART_FILETYPES = [
    ("Chart (SUS / USC)", "*.sus *.usc"),
    ("SUS chart", "*.sus"),
    ("USC chart", "*.usc"),
    ("All", "*.*"),
]


def chart_ext(path: str) -> str:
    return os.path.splitext(path)[1].lower()


def load_score(path: str):
    from rg_converters.converters import sus, usc

    ext = chart_ext(path)
    if ext not in CHART_EXTS:
        raise ValueError(f"unsupported chart type '{ext}' (use .sus or .usc)")
    with open(path, "r", encoding="utf-8") as f:
        return usc.load(f) if ext == ".usc" else sus.load(f)


def sus_text(path: str) -> str:
    """The chart as SUS text: a .sus as it is, a .usc converted with its TIL layers kept."""
    if chart_ext(path) == ".sus":
        with open(path, "r", encoding="utf-8") as f:
            return f.read()
    from rg_converters.converters import sus

    buf = io.StringIO()
    sus.export(buf, load_score(path), allow_layers=True)
    return buf.getvalue()


def combo_of_sus(text: str) -> int:
    from rg_converters.converters import sus

    return int(sus.load(io.StringIO(text)).combo_count)


def combo(path: str) -> int:
    return combo_of_sus(sus_text(path))
