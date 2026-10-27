from __future__ import annotations

import codecs
import gzip
import io
import json
import os
from typing import TYPE_CHECKING, Literal

if TYPE_CHECKING:
    from rg_converters.core.score import Score

ChartFormat = Literal["sus", "usc", "next_sekai"]

LEVELDATA_EXTS = (".json.gz", ".json", ".nsld", ".gz")
CHART_EXTS = (".sus", ".usc", *LEVELDATA_EXTS)
CHART_TYPES_TEXT = ".sus, .usc or Next SEKAI LevelData (.json, .json.gz, .nsld, .gz)"
CHART_FILETYPES = [
    ("Chart (SUS / USC / Next SEKAI)", "*.sus *.usc *.json *.gz *.nsld"),
    ("SUS chart", "*.sus"),
    ("USC chart", "*.usc"),
    ("Next SEKAI LevelData", "*.json *.gz *.nsld"),
    ("All", "*.*"),
]


class UnsupportedFeatures(ValueError):
    """Next SEKAI LevelData that only loads with error_on_unsupported_feature=False."""

    def __init__(self, path: str, features: list[str]):
        super().__init__(f"{os.path.basename(path)} uses unsupported Next SEKAI features")
        self.path = path
        self.features = features


def chart_ext(path: str) -> str:
    """The chart's extension, with a two-part one such as .json.gz kept whole."""
    name = os.path.basename(path).lower()
    for ext in CHART_EXTS:
        if name.endswith(ext):
            return ext
    return os.path.splitext(name)[1]


def is_leveldata(head: bytes) -> bool:
    """Gzip data or a JSON object, by content."""
    if head[:2] == b"\x1f\x8b":
        return True
    if head.startswith(codecs.BOM_UTF8):
        head = head[len(codecs.BOM_UTF8) :]
    return head.lstrip()[:1] == b"{"


def chart_format(path: str) -> ChartFormat:
    ext = chart_ext(path)
    if ext == ".sus":
        return "sus"
    if ext == ".usc":
        return "usc"
    if ext in LEVELDATA_EXTS:
        with open(path, "rb") as f:
            head = f.read(64)
        if not is_leveldata(head):
            raise ValueError(
                f"{os.path.basename(path)} is not Next SEKAI LevelData (expected gzip or a JSON object)"
            )
        return "next_sekai"
    raise ValueError(f"unsupported chart type '{ext}' (use {CHART_TYPES_TEXT})")


def _check_leveldata(raw: bytes) -> None:
    if raw[:2] == b"\x1f\x8b":
        try:
            raw = gzip.decompress(raw)
        except (OSError, EOFError) as e:
            raise ValueError(f"cannot decompress the LevelData: {e}") from e
    data = json.loads(raw.decode("utf-8-sig"))
    if not isinstance(data, dict) or not isinstance(data.get("entities"), list):
        raise ValueError("not Next SEKAI LevelData (no entities list)")


def _feature_lines(message: str) -> list[str]:
    lines = [ln.strip() for ln in message.splitlines()[1:] if ln.strip()]
    return lines or [message]


def load_score(path: str, strict: bool = True) -> Score:
    """Load any supported chart; `strict` only applies to Next SEKAI LevelData."""
    fmt = chart_format(path)
    if fmt == "next_sekai":
        from rg_converters.converters.LevelData import next_sekai

        with open(path, "rb") as f:
            raw = f.read()
        _check_leveldata(raw)
        if raw.startswith(codecs.BOM_UTF8):
            raw = raw[len(codecs.BOM_UTF8) :]
        try:
            return next_sekai.load(
                raw,
                error_on_unsupported_feature=strict,
                attempt_attached_note_convert=True,
            )
        except next_sekai.UnsupportedFeatureError as e:
            raise UnsupportedFeatures(path, _feature_lines(str(e))) from e

    from rg_converters.converters import sus, usc

    with open(path, "r", encoding="utf-8") as f:
        return usc.load(f) if fmt == "usc" else sus.load(f)


def note_count(score: Score) -> int:
    """The game's note count: rg's combo, plus damage notes (traces in game, judged the other way round)."""
    damage = sum(1 for n in score.notes if getattr(n, "type", None) == "damage" and not getattr(n, "fake", False))
    return int(score.combo_count) + damage


def sus_text(path: str) -> str:
    with open(path, "r", encoding="utf-8") as f:
        return f.read()


def leveldata_text(score: Score, from_sus: bool = False) -> str:
    """The score as uncompressed Next SEKAI LevelData (TextAsset.text cannot hold gzip).

    `from_sus` turns SUS guides into Next SEKAI guides: a smooth fade on their own layer.
    """
    from rg_converters.converters.LevelData import next_sekai

    buf = io.BytesIO()
    next_sekai.export(
        buf,
        score,
        as_compressed=False,
        smooth_guide_fade=from_sus,
        use_guide_layer=from_sus,
    )
    return buf.getvalue().decode("utf-8")


def chart_offset_seconds(score: Score) -> float:
    """The chart's own offset as the LevelData bgmOffset it exports to.

    rg-converters stores SUS #WAVEOFFSET (negated), USC offset and LevelData bgmOffset this way.
    """
    return float(score.metadata.waveoffset)


def offset_ms_for(score: Score) -> int:
    """The builder offset_ms (positive: the song starts later) matching the chart's own offset
    (positive bgmOffset: the chart starts that far into the song)."""
    return -round(chart_offset_seconds(score) * 1000)
