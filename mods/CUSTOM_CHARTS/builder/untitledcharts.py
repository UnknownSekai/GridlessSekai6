from __future__ import annotations

import http.client
import io
import json
import os
import re
import urllib.error
import urllib.parse
import urllib.request
from collections.abc import Callable
from dataclasses import dataclass
from email.message import Message
from typing import IO, Any

import charts

HOST = "untitledcharts.com"
SITE = f"https://{HOST}"
USER_AGENT = "GridlessSekai6-ChartBuilder"
TIMEOUT_SECONDS = 30
MAX_DOWNLOAD_BYTES = 512 * 1024 * 1024
LINK_EXAMPLE = f"{HOST}/levels/UnCh-…/"

_SCHEME = re.compile(r"^[A-Za-z][A-Za-z0-9+.-]*://")
_LEVEL_PATH = re.compile(r"/(?:sonolus/)?levels/([A-Za-z0-9_-][A-Za-z0-9_.-]*)/?")
_IMAGE_EXTS = {
    "PNG": ".png",
    "JPEG": ".jpg",
    "GIF": ".gif",
    "WEBP": ".webp",
    "BMP": ".bmp",
    "TIFF": ".tif",
    "TGA": ".tga",
}

Log = Callable[[str], None]


class UntitledChartsError(Exception):
    pass


@dataclass
class Level:
    name: str
    title: str
    artists: str
    author: str
    rating: int | None
    folder: str
    chart: str
    bgm: str
    preview: str | None
    cover: str


def level_name(url: str) -> str:
    """The level name from an untitledcharts.com level link; anything else raises ValueError."""
    text = url.strip()
    if not _SCHEME.match(text):
        text = "https://" + text
    parts = urllib.parse.urlsplit(text)
    if parts.scheme.lower() not in ("http", "https"):
        raise ValueError(f"Only http:// or https:// links to {HOST} are accepted.")
    if parts.netloc.lower() != HOST:
        raise ValueError(
            f"The link must be on {HOST}, not '{parts.netloc or url.strip()}'.\n"
            "Other sites, subdomains, ports and user names are not accepted."
        )
    m = _LEVEL_PATH.fullmatch(parts.path)
    if not m:
        raise ValueError(
            f"Not a level link. Expected {LINK_EXAMPLE} or {HOST}/sonolus/levels/UnCh-…/"
        )
    return m.group(1)


def level_page(name: str) -> str:
    return f"{SITE}/levels/{urllib.parse.quote(name)}/"


def _site_host(netloc: str) -> bool:
    host = netloc.lower()
    return host == HOST or host.endswith("." + HOST)


class _SiteRedirects(urllib.request.HTTPRedirectHandler):
    def redirect_request(
        self,
        req: urllib.request.Request,
        fp: IO[bytes],
        code: int,
        msg: str,
        headers: Message,
        newurl: str,
    ) -> urllib.request.Request | None:
        parts = urllib.parse.urlsplit(newurl)
        if parts.scheme not in ("http", "https") or not _site_host(parts.netloc):
            raise UntitledChartsError(f"Refusing to follow a redirect off {HOST}: {newurl}")
        return super().redirect_request(req, fp, code, msg, headers, newurl)


_OPENER = urllib.request.build_opener(_SiteRedirects)


def _server_message(e: urllib.error.HTTPError) -> str:
    try:
        body = json.loads(e.read(65536))
    except Exception:
        return ""
    msg = body.get("message") if isinstance(body, dict) else None
    return f" ({msg})" if isinstance(msg, str) and msg else ""


def _get(url: str, what: str, log: Log | None = None) -> bytes:
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    host = urllib.parse.urlsplit(url).netloc
    try:
        with _OPENER.open(req, timeout=TIMEOUT_SECONDS) as r:
            total = int(r.headers.get("Content-Length") or 0)
            chunks: list[bytes] = []
            got = 0
            while chunk := r.read(1 << 16):
                got += len(chunk)
                if got > MAX_DOWNLOAD_BYTES:
                    raise UntitledChartsError(
                        f"{what} is larger than {MAX_DOWNLOAD_BYTES >> 20} MB."
                    )
                chunks.append(chunk)
                if log and total > 1 << 20:
                    log(f"Downloading {what}: {got / 2**20:.1f} / {total / 2**20:.1f} MB")
            return b"".join(chunks)
    except urllib.error.HTTPError as e:
        raise UntitledChartsError(
            f"{host} answered HTTP {e.code} for {what}{_server_message(e)}."
        ) from e
    except urllib.error.URLError as e:
        raise UntitledChartsError(f"Could not connect to {host} for {what}: {e.reason}") from e
    except (OSError, http.client.HTTPException) as e:
        raise UntitledChartsError(f"Network error while downloading {what}: {e}") from e


def check_reachable() -> None:
    try:
        _get(f"{SITE}/sonolus/info", "the server info")
    except UntitledChartsError as e:
        raise UntitledChartsError(
            f"{HOST} is not reachable. Check your internet connection and try again.\n\n{e}"
        ) from e


def fetch_item(name: str) -> dict[str, Any]:
    raw = _get(f"{SITE}/sonolus/levels/{urllib.parse.quote(name)}/", f"level {name}")
    try:
        details = json.loads(raw)
    except ValueError as e:
        raise UntitledChartsError(f"{HOST} sent an unreadable answer for level {name}.") from e
    item = details.get("item") if isinstance(details, dict) else None
    if not isinstance(item, dict):
        raise UntitledChartsError(f"{HOST} sent no level details for {name}.")
    return item


def _srl(item: dict[str, Any], key: str) -> tuple[str, str] | None:
    """(hash, absolute url) of an item resource; relative urls are on the site."""
    srl = item.get(key)
    url = srl.get("url") if isinstance(srl, dict) else None
    if not isinstance(url, str) or not url.strip():
        return None
    full = urllib.parse.urljoin(SITE + "/", url.strip())
    if urllib.parse.urlsplit(full).scheme not in ("http", "https"):
        raise UntitledChartsError(f"The level's {key} has an unusable url: {url}")
    return str(srl.get("hash") or ""), full


def _required(item: dict[str, Any], key: str, what: str) -> tuple[str, str]:
    srl = _srl(item, key)
    if srl is None:
        raise UntitledChartsError(f"The level has no {what}.")
    return srl


def _audio_ext(raw: bytes, what: str) -> str:
    if raw[:3] == b"ID3":
        return ".mp3"
    if raw[:4] == b"OggS":
        return ".ogg"
    if raw[:4] == b"fLaC":
        return ".flac"
    if raw[:4] == b"RIFF" and raw[8:12] == b"WAVE":
        return ".wav"
    if raw[4:8] == b"ftyp":
        return ".m4a"
    if raw[:4] == b"\x1a\x45\xdf\xa3":
        return ".webm"
    if len(raw) > 1 and raw[0] == 0xFF and raw[1] & 0xE0 == 0xE0:
        return ".aac" if raw[1] & 0x06 == 0 else ".mp3"
    raise UntitledChartsError(f"The {what} is not in a recognised audio format.")


def _write(folder: str, filename: str, data: bytes) -> str:
    path = os.path.join(folder, filename)
    with open(path, "wb") as f:
        f.write(data)
    return path


def _save_cover(raw: bytes, folder: str) -> str:
    from PIL import Image

    try:
        with Image.open(io.BytesIO(raw)) as im:
            ext = _IMAGE_EXTS.get(im.format or "")
            if ext:
                im.verify()
                return _write(folder, "jacket" + ext, raw)
            path = os.path.join(folder, "jacket.png")
            im.convert("RGBA").save(path)
            return path
    except Exception as e:
        raise UntitledChartsError(f"The cover is not a readable image: {e}") from e


def _author(item: dict[str, Any]) -> str:
    author = str(item.get("author") or "").strip()
    return re.sub(r"#\d+$", "", author).strip() or author


def _rating(item: dict[str, Any]) -> int | None:
    rating = item.get("rating")
    if isinstance(rating, (int, float)) and not isinstance(rating, bool):
        return int(round(rating))
    return None


def download_level(name: str, folder: str, log: Log) -> Level:
    """Download a level's chart, song, preview and cover into `folder`.

    `preview` is None when the level has none or it is the song itself.
    """
    log(f"Checking that {HOST} is reachable…")
    check_reachable()
    log(f"Fetching level {name}…")
    item = fetch_item(name)
    data = _required(item, "data", "chart data")
    bgm = _required(item, "bgm", "song audio")
    cover = _required(item, "cover", "cover image")
    preview = _srl(item, "preview")
    os.makedirs(folder, exist_ok=True)

    raw = _get(data[1], "the chart", log)
    if not charts.is_leveldata(raw[:64]):
        raise UntitledChartsError("The level's chart is not Next SEKAI LevelData.")
    chart_path = _write(folder, "chart.json.gz" if raw[:2] == b"\x1f\x8b" else "chart.json", raw)

    raw = _get(bgm[1], "the song audio", log)
    bgm_path = _write(folder, "track" + _audio_ext(raw, "song audio"), raw)

    preview_path = None
    if preview and preview[1] != bgm[1] and not (preview[0] and preview[0] == bgm[0]):
        raw = _get(preview[1], "the preview audio", log)
        preview_path = _write(folder, "preview" + _audio_ext(raw, "preview audio"), raw)

    cover_path = _save_cover(_get(cover[1], "the cover", log), folder)

    return Level(
        name=name,
        title=str(item.get("title") or name).strip(),
        artists=str(item.get("artists") or "").strip(),
        author=_author(item),
        rating=_rating(item),
        folder=folder,
        chart=chart_path,
        bgm=bgm_path,
        preview=preview_path,
        cover=cover_path,
    )
