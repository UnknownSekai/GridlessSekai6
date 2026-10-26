from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import tempfile
import tkinter as tk
import uuid
import zipfile
from dataclasses import dataclass
from tkinter import filedialog, messagebox, ttk

import charts
import deps

DIFFICULTIES = ["easy", "normal", "hard", "expert", "master", "append"]
MV_TYPES = ["ogmv", "2dmv"]
VOCAL_TYPES = ["sekai", "original_song", "another_vocal"]
MIN_LEVEL, MAX_LEVEL = 1, 99
MAX_CHARTS = 6
MAX_PREVIEW_SECONDS = 45.0
FORMAT_VERSION = 1

IMAGE_EXTS = {".png", ".jpg", ".jpeg", ".bmp", ".gif", ".webp", ".tga", ".tif", ".tiff"}


def _run(cmd: list[str]) -> subprocess.CompletedProcess:
    flags = 0x08000000 if os.name == "nt" else 0
    return subprocess.run(cmd, capture_output=True, text=True, creationflags=flags)


def media_duration(path: str) -> float | None:
    cp = _run(
        [
            deps.FFPROBE,
            "-v",
            "error",
            "-show_entries",
            "format=duration",
            "-of",
            "default=noprint_wrappers=1:nokey=1",
            path,
        ]
    )
    if cp.returncode != 0:
        return None
    try:
        return float(cp.stdout.strip())
    except ValueError:
        return None


def has_stream(path: str, kind: str) -> bool:
    cp = _run(
        [
            deps.FFPROBE,
            "-v",
            "error",
            "-select_streams",
            kind[0],
            "-show_entries",
            "stream=codec_type",
            "-of",
            "csv=p=0",
            path,
        ]
    )
    return cp.returncode == 0 and kind in cp.stdout


def validate_image(path: str) -> tuple[bool, str]:
    from PIL import Image

    try:
        with Image.open(path) as im:
            im.verify()
        return True, ""
    except Exception as e:
        return False, f"Not a readable image: {e}"


_TIME_SIG_RE = re.compile(r"^#\d{3}02:", re.IGNORECASE)


def validate_sus_text(text: str) -> tuple[bool, str]:
    lines = [ln.strip() for ln in text.splitlines()]
    sig_lines = [ln for ln in lines if _TIME_SIG_RE.match(ln)]
    if len(sig_lines) != 1:
        return (
            False,
            f"must have exactly one time-signature line, found {len(sig_lines)}",
        )
    if not any(ln.replace(" ", "") == "#00002:4" for ln in lines):
        return False, "missing the 4/4 time signature at measure 0 (#00002: 4)"
    return True, ""


def gen_double_uuid() -> str:
    return f"{uuid.uuid4()}_{uuid.uuid4()}"


def validate_double_uuid(s: str) -> bool:
    parts = s.strip().split("_")
    if len(parts) != 2:
        return False
    for p in parts:
        try:
            u = uuid.UUID(p)
        except (ValueError, AttributeError):
            return False
        if u.version != 4 or str(u) != p.lower():
            return False
    return True


def ext_of(path: str) -> str:
    return os.path.splitext(path)[1].lower()


@dataclass
class ChartRow:
    difficulty: tk.StringVar
    level: tk.StringVar
    path: tk.StringVar
    combo: tk.StringVar


@dataclass
class MVRow:
    kind: tk.StringVar
    path: tk.StringVar


@dataclass
class AltVocal:
    name: tk.StringVar
    vocals: tk.StringVar
    vocal_type: tk.StringVar
    audio: tk.StringVar
    preview: tk.StringVar
    jacket: tk.StringVar


class App(tk.Tk):
    def __init__(self):
        super().__init__()
        self.title("GridlessSekai6 Custom Chart Builder")
        self.geometry("880x720")
        self.minsize(760, 600)

        self.chart_rows: list[ChartRow] = []
        self.mv_rows: list[MVRow] = []
        self.alt_vocals: list[AltVocal] = []
        self._import_tmp: str | None = None

        nb = ttk.Notebook(self)
        nb.pack(fill="both", expand=True, padx=8, pady=(8, 0))
        self._build_files_tab(nb)
        self._build_charts_tab(nb)
        self._build_metadata_tab(nb)
        self._build_vocals_tab(nb)
        self._build_identifier_tab(nb)

        bar = ttk.Frame(self)
        bar.pack(fill="x", padx=8, pady=8)
        self.status = tk.StringVar(value="Ready.")
        ttk.Label(bar, textvariable=self.status, anchor="w").pack(
            side="left", fill="x", expand=True
        )
        ttk.Button(bar, text="Build .zip…", command=self.build).pack(side="right")
        ttk.Button(bar, text="Compile .chart.gs6…", command=self.compile_gs6).pack(
            side="right", padx=6
        )
        ttk.Button(bar, text="Import .zip…", command=self.import_zip).pack(side="right")

        self._add_chart_row()

    def _build_files_tab(self, nb):
        tab = ttk.Frame(nb)
        nb.add(tab, text="Files")
        self.jacket = tk.StringVar()
        self.track = tk.StringVar()
        self.track_pre = tk.StringVar()

        self._file_picker(
            tab,
            0,
            "Jacket (image)*",
            self.jacket,
            [
                ("Images", "*.png *.jpg *.jpeg *.bmp *.gif *.webp *.tga *.tif *.tiff"),
                ("All", "*.*"),
            ],
        )
        self._file_picker(
            tab,
            1,
            "Audio track*",
            self.track,
            [("Audio", "*.wav *.mp3 *.flac *.ogg *.m4a *.aac *.opus"), ("All", "*.*")],
        )
        self._file_picker(
            tab,
            2,
            "Preview audio (≤45s)*",
            self.track_pre,
            [("Audio", "*.wav *.mp3 *.flac *.ogg *.m4a *.aac *.opus"), ("All", "*.*")],
        )

        ttk.Separator(tab, orient="horizontal").grid(
            row=3, column=0, columnspan=3, sticky="ew", pady=10
        )
        ttk.Label(tab, text="Music videos (optional, up to 2):").grid(
            row=4, column=0, columnspan=3, sticky="w", padx=8
        )
        self.mv_frame = ttk.Frame(tab)
        self.mv_frame.grid(row=5, column=0, columnspan=3, sticky="ew", padx=4)
        ttk.Button(tab, text="+ Add music video", command=self._add_mv_row).grid(
            row=6, column=0, sticky="w", padx=8, pady=4
        )
        tab.columnconfigure(1, weight=1)

    def _file_picker(self, parent, row, label, var, filetypes):
        ttk.Label(parent, text=label).grid(
            row=row, column=0, sticky="w", padx=8, pady=6
        )
        ttk.Entry(parent, textvariable=var).grid(
            row=row, column=1, sticky="ew", padx=4, pady=6
        )
        ttk.Button(
            parent, text="Browse…", command=lambda: self._browse(var, filetypes)
        ).grid(row=row, column=2, padx=8, pady=6)

    def _browse(self, var, filetypes):
        p = filedialog.askopenfilename(filetypes=filetypes)
        if p:
            var.set(p)

    def _add_mv_row(self):
        if len(self.mv_rows) >= 2:
            return
        r = MVRow(
            kind=tk.StringVar(value=MV_TYPES[len(self.mv_rows) % 2]),
            path=tk.StringVar(),
        )
        self.mv_rows.append(r)
        self._redraw_mv()

    def _redraw_mv(self):
        for w in self.mv_frame.winfo_children():
            w.destroy()
        for i, r in enumerate(self.mv_rows):
            ttk.Combobox(
                self.mv_frame,
                textvariable=r.kind,
                values=MV_TYPES,
                state="readonly",
                width=7,
            ).grid(row=i, column=0, padx=4, pady=3)
            ttk.Entry(self.mv_frame, textvariable=r.path, width=60).grid(
                row=i, column=1, sticky="ew", padx=4
            )
            ttk.Button(
                self.mv_frame,
                text="Browse…",
                command=lambda v=r.path: self._browse(
                    v,
                    [("Video", "*.mp4 *.mov *.mkv *.webm *.avi *.usm"), ("All", "*.*")],
                ),
            ).grid(row=i, column=2, padx=4)
            ttk.Button(
                self.mv_frame,
                text="✕",
                command=lambda rr=r: (self.mv_rows.remove(rr), self._redraw_mv()),
            ).grid(row=i, column=3, padx=4)
        self.mv_frame.columnconfigure(1, weight=1)

    def _build_charts_tab(self, nb):
        tab = ttk.Frame(nb)
        nb.add(tab, text="Charts")
        ttk.Label(
            tab,
            text="1-6 .sus or .usc charts. Combo is auto-derived on build.",
        ).grid(row=0, column=0, columnspan=5, sticky="w", padx=8, pady=6)
        hdr = ttk.Frame(tab)
        hdr.grid(row=1, column=0, columnspan=5, sticky="ew", padx=8)
        for i, t in enumerate(["Difficulty", "Level (1-99)", "Chart file", "Combo", ""]):
            ttk.Label(hdr, text=t, font=("", 9, "bold")).grid(
                row=0, column=i, sticky="w", padx=4
            )
        self.charts_frame = ttk.Frame(tab)
        self.charts_frame.grid(row=2, column=0, columnspan=5, sticky="nsew", padx=4)
        ttk.Button(tab, text="+ Add chart", command=self._add_chart_row).grid(
            row=3, column=0, sticky="w", padx=8, pady=6
        )
        tab.columnconfigure(0, weight=1)

    def _add_chart_row(self):
        if len(self.chart_rows) >= MAX_CHARTS:
            return
        used = {r.difficulty.get() for r in self.chart_rows}
        default = next((d for d in DIFFICULTIES if d not in used), DIFFICULTIES[0])
        r = ChartRow(
            difficulty=tk.StringVar(value=default),
            level=tk.StringVar(value="1"),
            path=tk.StringVar(),
            combo=tk.StringVar(value="-"),
        )
        self.chart_rows.append(r)
        self._redraw_charts()

    def _redraw_charts(self):
        for w in self.charts_frame.winfo_children():
            w.destroy()
        for i, r in enumerate(self.chart_rows):
            ttk.Combobox(
                self.charts_frame,
                textvariable=r.difficulty,
                values=DIFFICULTIES,
                state="readonly",
                width=9,
            ).grid(row=i, column=0, padx=4, pady=3)
            ttk.Spinbox(
                self.charts_frame,
                from_=MIN_LEVEL,
                to=MAX_LEVEL,
                textvariable=r.level,
                width=5,
            ).grid(row=i, column=1, padx=4)
            ttk.Entry(self.charts_frame, textvariable=r.path, width=46).grid(
                row=i, column=2, padx=4, sticky="ew"
            )
            ttk.Button(
                self.charts_frame,
                text="…",
                command=lambda v=r.path, rr=r: self._pick_chart(v, rr),
            ).grid(row=i, column=3, padx=2)
            ttk.Label(self.charts_frame, textvariable=r.combo, width=7).grid(
                row=i, column=4, padx=4
            )
            ttk.Button(
                self.charts_frame,
                text="✕",
                command=lambda rr=r: (
                    self.chart_rows.remove(rr),
                    self._redraw_charts(),
                ),
            ).grid(row=i, column=5, padx=4)
        self.charts_frame.columnconfigure(2, weight=1)

    def _pick_chart(self, var, row: ChartRow):
        p = filedialog.askopenfilename(filetypes=charts.CHART_FILETYPES)
        if not p:
            return
        var.set(p)
        try:
            row.combo.set(str(charts.combo(p)))
        except Exception as e:
            row.combo.set("err")
            self.status.set(f"Combo parse failed for {os.path.basename(p)}: {e}")

    def _build_metadata_tab(self, nb):
        tab = ttk.Frame(nb, padding=8)
        nb.add(tab, text="Metadata")

        self.title_t = tk.StringVar()
        self.lyricist = tk.StringVar()
        self.composer = tk.StringVar()
        self.arranger = tk.StringVar()
        self.artist = tk.StringVar()
        self.vocals = tk.StringVar()
        self.collab = tk.StringVar()
        self.charter = tk.StringVar()
        self.is_full = tk.BooleanVar(value=True)
        self.offset_ms = tk.StringVar(value="0")
        self.mv_offset_ms = tk.StringVar(value="0")
        self.original = tk.StringVar()

        row = [0]

        def str_field(label, var, hint=""):
            ttk.Label(tab, text=label).grid(
                row=row[0], column=0, sticky="e", padx=4, pady=3
            )
            ttk.Entry(tab, textvariable=var, width=48).grid(
                row=row[0], column=1, sticky="ew", padx=4, pady=3
            )
            if hint:
                ttk.Label(tab, text=hint, foreground="#888").grid(
                    row=row[0], column=2, sticky="w", padx=4
                )
            row[0] += 1

        str_field("Title*", self.title_t)
        str_field("Charter*", self.charter)
        str_field("Lyricist*", self.lyricist)
        str_field("Composer*", self.composer)
        str_field("Arranger*", self.arranger)
        str_field("Artist*", self.artist)
        str_field("Vocals*", self.vocals)
        ttk.Checkbutton(
            tab, text="Full-length (not a game-size cut)", variable=self.is_full
        ).grid(row=row[0], column=1, sticky="w", padx=4, pady=3)
        row[0] += 1
        str_field("offset_ms", self.offset_ms, "chart timing offset (int)")
        str_field("additional_mv_offset_ms", self.mv_offset_ms, "extra MV offset (int)")
        str_field("original (URL)", self.original, "optional")
        str_field("Collab (optional)", self.collab)
        tab.columnconfigure(1, weight=1)

    def _build_vocals_tab(self, nb):
        tab = ttk.Frame(nb, padding=8)
        nb.add(tab, text="Vocals")
        ttk.Label(
            tab,
            wraplength=840,
            justify="left",
            text=(
                "Alt vocals (covers). Leave empty for a single-vocal song. If you add alt vocals, "
                "name the DEFAULT vocal below (it uses the main track + jacket), then add each "
                "alternate with its own audio/preview and an optional jacket."
            ),
        ).grid(row=0, column=0, columnspan=4, sticky="w", pady=(0, 8))

        self.default_vocal_name = tk.StringVar()
        self.default_vocal_type = tk.StringVar(value="sekai")
        df = ttk.LabelFrame(tab, text="Default vocal (uses the main track)", padding=6)
        df.grid(row=1, column=0, columnspan=4, sticky="ew", pady=4)
        ttk.Label(df, text="Name").grid(row=0, column=0, sticky="e", padx=4)
        ttk.Entry(df, textvariable=self.default_vocal_name, width=28).grid(
            row=0, column=1, padx=4
        )
        ttk.Label(df, text="Type").grid(row=0, column=2, sticky="e", padx=4)
        ttk.Combobox(
            df,
            textvariable=self.default_vocal_type,
            values=VOCAL_TYPES,
            state="readonly",
            width=16,
        ).grid(row=0, column=3, padx=4)

        self.vocals_frame = ttk.Frame(tab)
        self.vocals_frame.grid(row=2, column=0, columnspan=4, sticky="nsew", pady=4)
        ttk.Button(tab, text="+ Add alt vocal", command=self._add_alt_vocal).grid(
            row=3, column=0, sticky="w", pady=6
        )
        tab.columnconfigure(0, weight=1)

    def _add_alt_vocal(self):
        self.alt_vocals.append(
            AltVocal(
                name=tk.StringVar(),
                vocals=tk.StringVar(),
                vocal_type=tk.StringVar(value="original_song"),
                audio=tk.StringVar(),
                preview=tk.StringVar(),
                jacket=tk.StringVar(),
            )
        )
        self._redraw_vocals()

    def _redraw_vocals(self):
        for w in self.vocals_frame.winfo_children():
            w.destroy()
        audio_ft = [
            ("Audio", "*.wav *.mp3 *.flac *.ogg *.m4a *.aac *.opus"),
            ("All", "*.*"),
        ]
        img_ft = [
            ("Images", "*.png *.jpg *.jpeg *.bmp *.gif *.webp *.tga *.tif *.tiff"),
            ("All", "*.*"),
        ]
        for i, v in enumerate(self.alt_vocals):
            f = ttk.LabelFrame(self.vocals_frame, text=f"Alt vocal {i + 1}", padding=6)
            f.pack(fill="x", pady=3)
            ttk.Label(f, text="Name").grid(row=0, column=0, sticky="e", padx=4, pady=2)
            ttk.Entry(f, textvariable=v.name, width=40).grid(
                row=0, column=1, sticky="ew", padx=4, pady=2
            )
            ttk.Label(f, text="Vocals").grid(
                row=1, column=0, sticky="e", padx=4, pady=2
            )
            ttk.Entry(f, textvariable=v.vocals, width=40).grid(
                row=1, column=1, sticky="ew", padx=4, pady=2
            )
            ttk.Label(f, text="Type").grid(row=2, column=0, sticky="e", padx=4)
            ttk.Combobox(
                f,
                textvariable=v.vocal_type,
                values=VOCAL_TYPES,
                state="readonly",
                width=16,
            ).grid(row=2, column=1, sticky="w", padx=4)
            for row, label, var, ft in [
                (3, "Audio", v.audio, audio_ft),
                (4, "Preview", v.preview, audio_ft),
                (5, "Jacket (opt)", v.jacket, img_ft),
            ]:
                ttk.Label(f, text=label).grid(
                    row=row, column=0, sticky="e", padx=4, pady=2
                )
                ttk.Entry(f, textvariable=var, width=40).grid(
                    row=row, column=1, sticky="ew", padx=4, pady=2
                )
                ttk.Button(
                    f, text="…", command=lambda vv=var, t=ft: self._browse(vv, t)
                ).grid(row=row, column=2, padx=2)
            ttk.Button(
                f,
                text="Remove",
                command=lambda vv=v: (
                    self.alt_vocals.remove(vv),
                    self._redraw_vocals(),
                ),
            ).grid(row=6, column=1, sticky="w", pady=4)
            f.columnconfigure(1, weight=1)

    def _build_identifier_tab(self, nb):
        tab = ttk.Frame(nb)
        nb.add(tab, text="Identity")

        self.identifier = tk.StringVar(value=gen_double_uuid())
        ttk.Label(
            tab,
            wraplength=820,
            justify="left",
            text=(
                "This identifier uniquely names the chart. Re-exporting with the same identifier "
                "updates that chart in the game instead of adding a duplicate. Keep this value "
                "after your first export so you can reuse it for updates. You may paste your own: "
                "it must be two valid v4 UUIDs joined by '_'."
            ),
        ).pack(anchor="w", padx=10, pady=10)
        rowf = ttk.Frame(tab)
        rowf.pack(fill="x", padx=10)
        ttk.Entry(rowf, textvariable=self.identifier, width=80).pack(
            side="left", fill="x", expand=True
        )
        ttk.Button(
            rowf,
            text="Regenerate",
            command=lambda: self.identifier.set(gen_double_uuid()),
        ).pack(side="left", padx=6)
        ttk.Button(rowf, text="Copy", command=self._copy_identifier).pack(side="left")

    def _copy_identifier(self):
        self.clipboard_clear()
        self.clipboard_append(self.identifier.get())
        self.status.set("Identifier copied to clipboard.")

    def _collect(self) -> dict | None:
        errs: list[str] = []

        def need(path, label):
            if not path or not os.path.isfile(path):
                errs.append(f"{label} is required.")
                return False
            return True

        if need(self.jacket.get(), "Jacket"):
            ok, msg = validate_image(self.jacket.get())
            if not ok:
                errs.append(f"Jacket: {msg}")

        if need(self.track.get(), "Audio track") and not has_stream(
            self.track.get(), "audio"
        ):
            errs.append("Audio track has no audio stream.")
        if need(self.track_pre.get(), "Preview audio"):
            dur = media_duration(self.track_pre.get())
            if dur is None:
                errs.append("Preview audio: could not read duration.")
            elif dur > MAX_PREVIEW_SECONDS + 0.05:
                errs.append(
                    f"Preview audio is {dur:.1f}s (max {MAX_PREVIEW_SECONDS:.0f}s)."
                )

        diffs = []
        seen = set()
        rows = [r for r in self.chart_rows if r.path.get().strip()]
        if not rows:
            errs.append("At least one .sus or .usc chart is required.")
        for r in rows:
            d = r.difficulty.get()
            if d in seen:
                errs.append(f"Duplicate difficulty '{d}'.")
            seen.add(d)
            if not os.path.isfile(r.path.get()):
                errs.append(f"{d}: chart file not found.")
                continue
            if charts.chart_ext(r.path.get()) not in charts.CHART_EXTS:
                errs.append(f"{d}: chart must be a .sus or .usc file.")
                continue
            try:
                lvl = int(r.level.get())
            except ValueError:
                errs.append(f"{d}: level must be a number.")
                continue
            if not (MIN_LEVEL <= lvl <= MAX_LEVEL):
                errs.append(f"{d}: level must be {MIN_LEVEL}-{MAX_LEVEL}.")
            try:
                text = charts.sus_text(r.path.get())
            except Exception as e:
                errs.append(f"{d}: cannot read the chart ({e}).")
                continue
            ok, msg = validate_sus_text(text)
            if not ok:
                errs.append(f"{d}: {msg}.")
            try:
                combo = charts.combo_of_sus(text)
            except Exception as e:
                errs.append(f"{d}: combo derivation failed ({e}).")
                continue
            r.combo.set(str(combo))
            diffs.append(
                {"difficulty": d, "level": lvl, "notes": combo, "_path": r.path.get()}
            )

        og_mv = next(
            (
                r.path.get()
                for r in self.mv_rows
                if r.kind.get() == "ogmv" and r.path.get().strip()
            ),
            None,
        )
        two_d_mv = next(
            (
                r.path.get()
                for r in self.mv_rows
                if r.kind.get() == "2dmv" and r.path.get().strip()
            ),
            None,
        )
        for label, p in (("ogmv", og_mv), ("2dmv", two_d_mv)):
            if p:
                if not os.path.isfile(p):
                    errs.append(f"{label}: file not found.")
                elif not has_stream(p, "video"):
                    errs.append(f"{label}: no video stream.")

        def req_str(var, label):
            if not var.get().strip():
                errs.append(f"{label} is required.")

        req_str(self.title_t, "Title")
        if not self.charter.get().strip():
            errs.append("Charter is required.")
        for var, lab in [
            (self.lyricist, "Lyricist"),
            (self.composer, "Composer"),
            (self.arranger, "Arranger"),
            (self.artist, "Artist"),
            (self.vocals, "Vocals"),
        ]:
            req_str(var, lab)

        def parse_int(var, label, default=0):
            s = var.get().strip()
            if not s:
                return default
            try:
                return int(s)
            except ValueError:
                errs.append(f"{label} must be an integer.")
                return default

        offset = parse_int(self.offset_ms, "offset_ms")
        mv_offset = parse_int(self.mv_offset_ms, "additional_mv_offset_ms")

        mv_kinds = [r.kind.get() for r in self.mv_rows if r.path.get().strip()]
        if mv_kinds.count("ogmv") > 1:
            errs.append(
                "Only one 'ogmv' music video is allowed (ogmv/ogmv is unsupported)."
            )
        if mv_kinds.count("2dmv") > 1:
            errs.append(
                "Only one '2dmv' music video is allowed (2dmv/2dmv is unsupported)."
            )

        cover_files = []
        alt_entries = []
        alts = [
            v
            for v in self.alt_vocals
            if v.name.get().strip() or v.audio.get().strip() or v.vocals.get().strip()
        ]
        if alts and not self.default_vocal_name.get().strip():
            errs.append("Default vocal name is required when alt vocals are present.")
        for idx, v in enumerate(alts, start=1):
            nm, vo = v.name.get().strip(), v.vocals.get().strip()
            au, pv, jk = (
                v.audio.get().strip(),
                v.preview.get().strip(),
                v.jacket.get().strip(),
            )
            if not nm:
                errs.append(f"Alt vocal {idx}: name is required.")
            if not vo:
                errs.append(f"Alt vocal {idx}: vocals are required.")
            if not au or not os.path.isfile(au):
                errs.append(f"Alt vocal {idx}: audio file is required.")
            elif not has_stream(au, "audio"):
                errs.append(f"Alt vocal {idx}: audio has no audio stream.")
            if not pv or not os.path.isfile(pv):
                errs.append(f"Alt vocal {idx}: preview is required.")
            else:
                d = media_duration(pv)
                if d is not None and d > MAX_PREVIEW_SECONDS + 0.05:
                    errs.append(
                        f"Alt vocal {idx}: preview is {d:.1f}s (max {MAX_PREVIEW_SECONDS:.0f}s)."
                    )
            has_jacket = bool(jk)
            if has_jacket:
                if not os.path.isfile(jk):
                    errs.append(f"Alt vocal {idx}: jacket not found.")
                else:
                    ok, msg = validate_image(jk)
                    if not ok:
                        errs.append(f"Alt vocal {idx}: jacket {msg}")
            alt_entries.append(
                {
                    "id": idx,
                    "name": nm,
                    "vocals": vo,
                    "vocal_type": v.vocal_type.get(),
                    "has_jacket": has_jacket,
                }
            )
            if au:
                cover_files.append((au, f"covers/cover_{idx}" + ext_of(au)))
            if pv:
                cover_files.append((pv, f"covers/cover_pre_{idx}" + ext_of(pv)))
            if jk:
                cover_files.append((jk, f"covers/cover_jacket_{idx}" + ext_of(jk)))

        song_duration = None
        if self.track.get().strip():
            song_duration = media_duration(self.track.get())
            if song_duration is not None:
                song_duration = round(song_duration, 3)

        if not validate_double_uuid(self.identifier.get()):
            errs.append("Identifier must be two valid v4 UUIDs joined by '_'.")

        if errs:
            messagebox.showerror("Cannot build", "\n".join("• " + e for e in errs))
            return None

        def tr(var):
            v = var.get().strip()
            return {"jp": v, "en": v}

        info = {
            "identifier": self.identifier.get().strip(),
            "format_version": FORMAT_VERSION,
            "title": tr(self.title_t),
            "charter": self.charter.get().strip(),
            "difficulties": [
                {
                    "difficulty": d["difficulty"],
                    "level": d["level"],
                    "notes": d["notes"],
                }
                for d in diffs
            ],
            "lyricist": tr(self.lyricist),
            "composer": tr(self.composer),
            "arranger": tr(self.arranger),
            "artist": tr(self.artist),
            "vocals": tr(self.vocals),
            "isFullLength": self.is_full.get(),
            "vocaloid_or_other": "other",
            "offset_ms": offset,
            "additional_mv_offset_ms": mv_offset,
            "song_duration": song_duration,
            "original": self.original.get().strip() or None,
            "original_music_video": og_mv is not None,
            "2d_music_video": two_d_mv is not None,
        }
        if self.collab.get().strip():
            info["collab"] = tr(self.collab)

        if alt_entries:

            def d2(s):
                return {"jp": s, "en": s}

            covers = [
                {
                    "default": True,
                    "name": d2(self.default_vocal_name.get().strip()),
                    "vocal_type": self.default_vocal_type.get(),
                }
            ]
            for a in alt_entries:
                covers.append(
                    {
                        "default": False,
                        "id": a["id"],
                        "name": d2(a["name"]),
                        "vocals": d2(a["vocals"]),
                        "vocal_type": a["vocal_type"],
                        "has_jacket": a["has_jacket"],
                    }
                )
            info["covers"] = covers

        return {
            "info": info,
            "diffs": diffs,
            "jacket": self.jacket.get(),
            "track": self.track.get(),
            "track_pre": self.track_pre.get(),
            "ogmv": og_mv,
            "2dmv": two_d_mv,
            "cover_files": cover_files,
        }

    def build(self):
        data = self._collect()
        if not data:
            return
        default_name = (
            data["info"]["title"]["en"] or data["info"]["title"]["jp"] or "custom_chart"
        )
        default_name = (
            re.sub(r"[^\w\-. ]+", "_", default_name).strip() or "custom_chart"
        )
        out = filedialog.asksaveasfilename(
            defaultextension=".zip",
            initialfile=default_name + ".zip",
            filetypes=[("Zip", "*.zip")],
        )
        if not out:
            return
        try:
            self._write_zip(out, data)
        except Exception as e:
            messagebox.showerror("Build failed", str(e))
            return
        self.status.set(f"Built {os.path.basename(out)}")
        messagebox.showinfo(
            "Done",
            f"Built:\n{out}\n\nKeep the ZIP for when you want to update the chart.",
        )

    def _write_zip(self, out: str, data: dict):
        info = data["info"]
        with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED) as z:
            z.writestr("ChartInfo.json", json.dumps(info, ensure_ascii=False, indent=2))
            z.write(data["jacket"], "jacket" + ext_of(data["jacket"]))
            z.write(data["track"], "track" + ext_of(data["track"]))
            z.write(data["track_pre"], "track_pre" + ext_of(data["track_pre"]))
            for d in data["diffs"]:
                z.write(d["_path"], d["difficulty"] + charts.chart_ext(d["_path"]))
            if data["ogmv"]:
                z.write(data["ogmv"], "mv" + ext_of(data["ogmv"]))
            if data["2dmv"]:
                z.write(data["2dmv"], "2dmv" + ext_of(data["2dmv"]))
            for src, zip_name in data.get("cover_files", []):
                z.write(src, zip_name)

    def _find(self, stem: str) -> str | None:
        if not self._import_tmp:
            return None
        for root, _dirs, files in os.walk(self._import_tmp):
            for fn in files:
                if os.path.splitext(fn)[0] == stem:
                    return os.path.join(root, fn)
        return None

    def import_zip(self):
        path = filedialog.askopenfilename(
            title="Import a built chart .zip",
            filetypes=[("Chart zip", "*.zip"), ("All", "*.*")],
        )
        if not path:
            return
        try:
            self._load_from_zip(path)
        except Exception as e:
            messagebox.showerror("Import failed", str(e))
            return
        self.status.set(f"Imported {os.path.basename(path)}, fields repopulated.")

    def _load_from_zip(self, path: str):
        if self._import_tmp and os.path.isdir(self._import_tmp):
            shutil.rmtree(self._import_tmp, ignore_errors=True)
        self._import_tmp = tempfile.mkdtemp(prefix="gs6chart_")
        with zipfile.ZipFile(path) as z:
            z.extractall(self._import_tmp)
        info_path = os.path.join(self._import_tmp, "ChartInfo.json")
        if not os.path.isfile(info_path):
            raise ValueError("ChartInfo.json not found in the zip.")
        with open(info_path, "r", encoding="utf-8") as f:
            info = json.load(f)

        def one(v) -> str:
            if isinstance(v, dict):
                return v.get("en") or v.get("jp") or ""
            return v or ""

        self.jacket.set(self._find("jacket") or "")
        self.track.set(self._find("track") or "")
        self.track_pre.set(self._find("track_pre") or "")

        self.mv_rows.clear()
        if self._find("mv"):
            self.mv_rows.append(
                MVRow(
                    kind=tk.StringVar(value="ogmv"),
                    path=tk.StringVar(value=self._find("mv")),
                )
            )
        if self._find("2dmv"):
            self.mv_rows.append(
                MVRow(
                    kind=tk.StringVar(value="2dmv"),
                    path=tk.StringVar(value=self._find("2dmv")),
                )
            )
        self._redraw_mv()

        self.chart_rows.clear()
        for d in info.get("difficulties", []):
            diff = d.get("difficulty", "expert")
            self.chart_rows.append(
                ChartRow(
                    difficulty=tk.StringVar(value=diff),
                    level=tk.StringVar(value=str(d.get("level", 1))),
                    path=tk.StringVar(value=self._find(diff) or ""),
                    combo=tk.StringVar(value=str(d.get("notes", "-"))),
                )
            )
        if not self.chart_rows:
            self._add_chart_row()
        else:
            self._redraw_charts()

        self.title_t.set(one(info.get("title")))
        self.charter.set(info.get("charter", "") or "")
        self.lyricist.set(one(info.get("lyricist")))
        self.composer.set(one(info.get("composer")))
        self.arranger.set(one(info.get("arranger")))
        self.artist.set(one(info.get("artist")))
        self.vocals.set(one(info.get("vocals")))
        self.collab.set(one(info.get("collab")))
        self.is_full.set(bool(info.get("isFullLength", True)))
        self.offset_ms.set(str(info.get("offset_ms", 0)))
        self.mv_offset_ms.set(str(info.get("additional_mv_offset_ms", 0)))
        self.original.set(info.get("original") or "")

        if info.get("identifier"):
            self.identifier.set(info["identifier"])

        self.alt_vocals.clear()
        self.default_vocal_name.set("")
        self.default_vocal_type.set("sekai")
        for c in info.get("covers", []):
            if c.get("default"):
                self.default_vocal_name.set(one(c.get("name")))
                self.default_vocal_type.set(c.get("vocal_type", "sekai"))
            else:
                cid = c.get("id")
                self.alt_vocals.append(
                    AltVocal(
                        name=tk.StringVar(value=one(c.get("name"))),
                        vocals=tk.StringVar(value=one(c.get("vocals"))),
                        vocal_type=tk.StringVar(
                            value=c.get("vocal_type", "original_song")
                        ),
                        audio=tk.StringVar(value=self._find(f"cover_{cid}") or ""),
                        preview=tk.StringVar(
                            value=self._find(f"cover_pre_{cid}") or ""
                        ),
                        jacket=tk.StringVar(
                            value=self._find(f"cover_jacket_{cid}") or ""
                        ),
                    )
                )
        self._redraw_vocals()

    def compile_gs6(self):
        data = self._collect()
        if not data:
            return
        import encode

        bases = deps.BASES
        missing = encode.missing_bases(bases, want_mv=(data["ogmv"] or data["2dmv"]))
        if missing:
            messagebox.showerror(
                "Missing base bundles",
                "These base bundles are required but not found in the bases folder:\n\n"
                + "\n".join(missing),
            )
            return
        default_name = (
            re.sub(
                r"[^\w\-. ]+",
                "_",
                (
                    data["info"]["title"]["en"]
                    or data["info"]["title"]["jp"]
                    or "custom_chart"
                ),
            ).strip()
            or "custom_chart"
        )
        out = filedialog.asksaveasfilename(
            title="Choose a base name (writes .android.chart.gs6 and .ios.chart.gs6)",
            initialfile=default_name,
            filetypes=[("Compiled chart", "*.chart.gs6"), ("All", "*.*")],
        )
        if not out:
            return
        base = out
        for suf in (".android", ".ios"):
            for ext in (".chart.gs6", ".gs6", ".zip"):
                if base.lower().endswith(suf + ext):
                    base = base[: -len(suf + ext)]
        for ext in (".chart.gs6", ".gs6", ".zip"):
            if base.lower().endswith(ext):
                base = base[: -len(ext)]
        for suf in (".android", ".ios"):
            if base.lower().endswith(suf):
                base = base[: -len(suf)]
        self._run_compile(data, bases, base)

    def _run_compile(self, data: dict, bases: str, out: str):
        import threading

        import encode

        self.status.set("Compiling .chart.gs6, this can take a minute...")
        self.config(cursor="watch")

        def worker():
            try:
                outputs = encode.compile_chart(
                    data, bases, out, log=lambda m: self.after(0, self.status.set, m)
                )
            except Exception as e:
                import traceback

                tb = traceback.format_exc()
                self.after(0, self._compile_failed, str(e), tb)
                return
            self.after(0, self._compile_done, outputs, data["info"]["identifier"])

        threading.Thread(target=worker, daemon=True).start()

    def _compile_done(self, outputs: list[str], identifier: str):
        self.config(cursor="")
        self.status.set("Compiled " + ", ".join(os.path.basename(o) for o in outputs))
        messagebox.showinfo(
            "Done",
            "Compiled (one encrypted bundle set per platform):\n"
            + "\n".join(outputs)
            + f"\n\nIdentifier (save this to update the chart later):\n{identifier}",
        )

    def _compile_failed(self, msg: str, tb: str):
        self.config(cursor="")
        self.status.set(f"Compile failed: {msg}")
        messagebox.showerror("Compile failed", f"{msg}\n\n{tb}")


def report_problems(found: list[str]) -> None:
    text = (
        "The chart builder cannot start:\n\n"
        + "\n".join("• " + p for p in found)
        + "\n\n"
        + deps.FIXES
    )
    print(text)
    root = tk.Tk()
    root.withdraw()
    messagebox.showerror("Missing requirements", text)
    root.destroy()


if __name__ == "__main__":
    found = deps.problems()
    if found:
        report_problems(found)
        raise SystemExit(1)
    App().mainloop()
