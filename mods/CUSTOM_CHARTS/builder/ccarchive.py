from __future__ import annotations

import http.client
import json
import os
import re
import urllib.error
import urllib.parse
import urllib.request
from email.message import Message
from typing import IO, Any

import charts
from untitledcharts import Level, Log, _audio_ext, _author, _rating, _save_cover, _write

HOST = "cc.milkbun.org"
SITE = f"https://{HOST}"
# the archive's Sonolus server: cc.milkbun.org/sonolus/... redirects there
HOSTS = (HOST, "cc-backend.milkbun.org")
USER_AGENT = "GridlessSekai6-ChartBuilder"
TIMEOUT_SECONDS = 30
MAX_DOWNLOAD_BYTES = 512 * 1024 * 1024
LINK_EXAMPLE = f"{HOST}/charts/chcy-…"
PREFIX = "chcy-"

_SCHEME = re.compile(r"^[A-Za-z][A-Za-z0-9+.-]*://")
_LEVEL_PATH = re.compile(r"/(?:charts|levels|sonolus/levels)/([A-Za-z0-9_-]+)/?")


class CCArchiveError(Exception):
    pass


def level_name(url: str) -> str:
    """The Sonolus level name (chcy-…) from a CC Archive chart link; anything else raises ValueError."""
    text = url.strip()
    if not _SCHEME.match(text):
        text = "https://" + text
    parts = urllib.parse.urlsplit(text)
    if parts.scheme.lower() not in ("http", "https"):
        raise ValueError(f"Only http:// or https:// links to {HOST} are accepted.")
    if parts.netloc.lower() not in HOSTS:
        raise ValueError(
            f"The link must be on {HOST}, not '{parts.netloc or url.strip()}'.\n"
            "Other sites, subdomains, ports and user names are not accepted."
        )
    m = _LEVEL_PATH.fullmatch(parts.path)
    if not m:
        raise ValueError(f"Not a chart link. Expected {LINK_EXAMPLE} or {HOST}/sonolus/levels/chcy-…")
    name = m.group(1)
    return name if name.startswith(PREFIX) else PREFIX + name


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
        if parts.scheme not in ("http", "https") or parts.netloc.lower() not in HOSTS:
            raise CCArchiveError(f"Refusing to follow a redirect off {HOST}: {newurl}")
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
                    raise CCArchiveError(f"{what} is larger than {MAX_DOWNLOAD_BYTES >> 20} MB.")
                chunks.append(chunk)
                if log and total > 1 << 20:
                    log(f"Downloading {what}: {got / 2**20:.1f} / {total / 2**20:.1f} MB")
            return b"".join(chunks)
    except urllib.error.HTTPError as e:
        raise CCArchiveError(f"{host} answered HTTP {e.code} for {what}{_server_message(e)}.") from e
    except urllib.error.URLError as e:
        raise CCArchiveError(f"Could not connect to {host} for {what}: {e.reason}") from e
    except (OSError, http.client.HTTPException) as e:
        raise CCArchiveError(f"Network error while downloading {what}: {e}") from e


def check_reachable() -> None:
    try:
        _get(f"{SITE}/sonolus/info", "the server info")
    except CCArchiveError as e:
        raise CCArchiveError(
            f"{HOST} is not reachable. Check your internet connection and try again.\n\n{e}"
        ) from e


def fetch_item(name: str) -> dict[str, Any]:
    raw = _get(f"{SITE}/sonolus/levels/{urllib.parse.quote(name)}", f"chart {name}")
    try:
        details = json.loads(raw)
    except ValueError as e:
        raise CCArchiveError(f"{HOST} sent an unreadable answer for chart {name}.") from e
    item = details.get("item") if isinstance(details, dict) else None
    if not isinstance(item, dict):
        raise CCArchiveError(f"{HOST} sent no chart details for {name}.")
    return item


def _srl(item: dict[str, Any], key: str) -> tuple[str, str] | None:
    """(hash, absolute url) of an item resource; relative urls are on the site."""
    srl = item.get(key)
    url = srl.get("url") if isinstance(srl, dict) else None
    if not isinstance(url, str) or not url.strip():
        return None
    full = urllib.parse.urljoin(SITE + "/", url.strip())
    if urllib.parse.urlsplit(full).scheme not in ("http", "https"):
        raise CCArchiveError(f"The chart's {key} has an unusable url: {url}")
    return str(srl.get("hash") or ""), full


def _required(item: dict[str, Any], key: str, what: str) -> tuple[str, str]:
    srl = _srl(item, key)
    if srl is None:
        raise CCArchiveError(f"The chart has no {what}.")
    return srl


def download_level(name: str, folder: str, log: Log) -> Level:
    """Download a chart's level data, song, preview and cover into `folder`.

    `preview` is None when the chart has none or it is the song itself.
    """
    log(f"Checking that {HOST} is reachable…")
    check_reachable()
    log(f"Fetching chart {name}…")
    item = fetch_item(name)
    data = _required(item, "data", "chart data")
    bgm = _required(item, "bgm", "song audio")
    cover = _required(item, "cover", "cover image")
    preview = _srl(item, "preview")
    os.makedirs(folder, exist_ok=True)

    raw = _get(data[1], "the chart", log)
    if not charts.is_leveldata(raw[:64]) or charts.leveldata_format(raw) != "chart_cyanvas":
        raise CCArchiveError("The chart's data is not Chart Cyanvas LevelData.")
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
