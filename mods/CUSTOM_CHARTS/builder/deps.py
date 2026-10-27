from __future__ import annotations

import importlib
import importlib.metadata
import os
import re
import shutil

HERE = os.path.dirname(os.path.abspath(__file__))
BASES = os.path.join(HERE, "bases")

# (pip name, import name, minimum version); keep in sync with requirements.txt
PACKAGES: list[tuple[str, str, tuple[int, ...] | None]] = [
    ("Pillow", "PIL", None),
    ("rg-converters", "rg_converters", (2, 1, 2)),
    ("UnityPy", "UnityPy", None),
    ("cricodecs", "cricodecs", (1, 2, 0)),
]


def find_tool(name: str) -> str | None:
    """`name` on PATH, else next to the builder; it is not looked for anywhere else."""
    on_path = shutil.which(name)
    if on_path:
        return on_path
    local = os.path.join(HERE, name + (".exe" if os.name == "nt" else ""))
    return local if os.path.isfile(local) else None


FFMPEG = find_tool("ffmpeg")
FFPROBE = find_tool("ffprobe")


def _version(text: str) -> tuple[int, ...]:
    return tuple(int(part) for part in re.findall(r"\d+", text)[:3])


def _dotted(version: tuple[int, ...]) -> str:
    return ".".join(map(str, version))


def problems() -> list[str]:
    out: list[str] = []
    for tool, path in (("ffmpeg", FFMPEG), ("ffprobe", FFPROBE)):
        if not path:
            out.append(f"{tool} was not found on PATH or next to main.py.")
    for pip_name, module, minimum in PACKAGES:
        try:
            importlib.import_module(module)
        except Exception as e:
            out.append(f"{pip_name} is not installed or failed to load ({e}).")
            continue
        if minimum is None:
            continue
        try:
            installed = importlib.metadata.version(pip_name)
        except importlib.metadata.PackageNotFoundError:
            out.append(f"{pip_name} {_dotted(minimum)} or newer is required (installed version unknown).")
            continue
        if _version(installed) < minimum:
            out.append(f"{pip_name} {_dotted(minimum)} or newer is required, {installed} is installed.")

    import encode

    missing = encode.missing_bases(BASES)
    if missing:
        out.append("Base bundles missing from the bases folder: " + ", ".join(missing))
    return out


FIXES = (
    "Install the Python packages with:  pip install -U -r requirements.txt\n"
    "Install FFmpeg (ffmpeg.org) and put it on PATH, or copy ffmpeg and ffprobe next to main.py.\n"
    "The bases folder is required, download the whole builder folder."
)
