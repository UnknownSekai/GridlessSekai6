from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import struct
import subprocess
import tempfile
import zipfile
from collections.abc import Callable
from pathlib import Path

import charts
import deps

CACHE_DIR_NAME = "GS6CustomChart"
LEAD_SILENCE_SECONDS = 9
MV_BLACK_SECONDS = 9
PLATFORMS = ("android", "ios")

Log = Callable[[str], None]


def _bases(bases: str, platform: str) -> dict[str, str]:
    root = Path(bases)
    return {
        "jacket": (root / f"jacket_{platform}").as_posix(),
        "music_scores": (root / f"music_score_{platform}").as_posix(),
        "long": (root / f"long_{platform}").as_posix(),
        "short": (root / f"short_{platform}").as_posix(),
        "original_mv": (root / f"original_mv_{platform}").as_posix(),
        "sekai_mv": (root / f"sekai_mv_{platform}").as_posix(),
        "acb_long": (root / "example_long.acb").as_posix(),
        "acb_short": (root / "example_short.acb").as_posix(),
    }


def missing_bases(bases: str, want_mv: bool = False) -> list[str]:
    needed = ["jacket", "music_scores", "long", "short", "acb_long", "acb_short"]
    if want_mv:
        needed += ["original_mv", "sekai_mv"]
    out: list[str] = []
    for plat in PLATFORMS:
        paths = _bases(bases, plat)
        for key in needed:
            p = paths[key]
            if not os.path.isfile(p):
                out.append(os.path.relpath(p, bases))
    return sorted(set(out))


def _ffmpeg(cmd: list[str]) -> None:
    flags = 0x08000000 if os.name == "nt" else 0
    subprocess.run(
        [deps.FFMPEG, "-hide_banner", "-loglevel", "error", "-y", *cmd],
        check=True,
        creationflags=flags,
    )


def _transcode_wav(src: str, dst: str, offset_ms: int, silence_seconds: float) -> None:
    if silence_seconds == 0:
        silence_seconds = 0.001
    if offset_ms:
        silence_seconds += offset_ms / 1000
    pre: list[str] = ["-i", src]
    if silence_seconds > 0:
        delay = int(silence_seconds * 1000)
        pre += ["-af", f"adelay={delay}|{delay}"]
    elif silence_seconds < 0:
        pre += ["-af", f"atrim=start={abs(silence_seconds)},asetpts=PTS-STARTPTS"]
    _ffmpeg(
        [
            *pre,
            "-acodec",
            "pcm_s16le",
            "-ac",
            "2",
            "-ar",
            "44100",
            "-map_metadata",
            "-1",
            "-fflags",
            "+bitexact",
            "-flags:a",
            "+bitexact",
            "-f",
            "wav",
            dst,
        ]
    )


def _transcode_m1v(
    src: str, dst: str, offset_ms: int, w: int = 1052, h: int = 648, fps: int = 24
) -> None:
    blank = MV_BLACK_SECONDS + (offset_ms / 1000 if offset_ms else 0)
    if blank < 0:
        raise ValueError("negative MV lead time is unsupported")
    if blank == 0:
        blank = 0.001
    _ffmpeg(
        [
            "-f",
            "lavfi",
            "-t",
            str(blank),
            "-i",
            f"color=size={w}x{h}:rate={fps}:color=black",
            "-i",
            src,
            "-filter_complex",
            f"[0:v]fps={fps},scale={w}:{h},setsar=1,format=yuv420p[black];"
            f"[1:v]fps={fps},scale={w}:{h},setsar=1,format=yuv420p[video];"
            f"[black][video]concat=n=2:v=1:a=0[outv]",
            "-map",
            "[outv]",
            "-an",
            "-map_metadata",
            "-1",
            "-c:v",
            "mpeg1video",
            dst,
        ]
    )


def _encrypt_ab(ab_data: bytes) -> bytes:
    if not ab_data.startswith(b"UnityF"):
        raise ValueError("not a Unity AssetBundle")
    out = bytearray(b"\x10\x00\x00\x00")
    header = bytearray(ab_data[:128])
    for i in range(0, 128, 8):
        for j in range(5):
            header[i + j] ^= 0xFF
    out.extend(header)
    out.extend(ab_data[128:])
    return bytes(out)


def _save_as_unique_bundle(file) -> bytes:
    import UnityPy
    from UnityPy.files import SerializedFile

    def cab(ab_name: str) -> str:
        return "CAB-" + hashlib.md5(ab_name.encode("utf-8")).hexdigest()

    src = file.save(packer="original")
    env = UnityPy.load(src)
    remap: dict[str, str] = {}
    for name, f in env.file.files.items():
        if isinstance(f, SerializedFile):
            remap[name] = cab(f.assetbundle.m_Name)
            f.save()
    for old, new in remap.items():
        if old in env.file.files:
            env.file.files[new] = env.file.files[old]
            del env.file.files[old]
    return env.file.save(packer="original")


def _copy_object_reader(src, new_pathid: int):
    from UnityPy.files import ObjectReader

    obj = ObjectReader.__new__(ObjectReader)
    for a in (
        "type_id",
        "class_id",
        "serialized_type",
        "stripped",
        "is_destroyed",
        "assets_file",
        "reader",
        "byte_start",
        "byte_size",
        "version",
        "version2",
    ):
        setattr(obj, a, getattr(src, a))
    obj.path_id = new_pathid
    obj.data = b""
    return obj


def _copy_pptr(src, file_id: int, path_id: int):
    from UnityPy.classes import PPtr

    pptr = PPtr.__new__(PPtr)
    pptr.assetsfile = src.assetsfile
    pptr.m_FileID = file_id
    pptr.m_PathID = path_id
    return pptr


def _copy_assetinfo(src, asset, preload_index: int, preload_size: int):
    from UnityPy.classes import AssetInfo

    info = AssetInfo.__new__(AssetInfo)
    info.asset = asset
    info.preloadIndex = preload_index
    info.preloadSize = preload_size
    return info


def _transcode_acb(dst: str, cue_name: str, wav: str, template_acb: str) -> None:
    from cricodecs import awb, hca
    from cricodecs import wav as wavio

    hca_bytes = hca.encode(wavio.load(wav))
    fmt = hca.load(hca_bytes).header().fmt
    num_samples = fmt.frame_count * 1024
    length_ms = int(num_samples * 1000 / fmt.sample_rate)

    name, acb = _parse_utf(Path(template_acb).read_bytes())
    row = acb[0]

    src_awb = awb.load(row["AwbFile"][1])
    out_awb = awb.create(
        version=src_awb.version,
        alignment=src_awb.alignment,
        subkey=src_awb.subkey,
        id_size=src_awb.id_size,
        offset_size=src_awb.offset_size,
    )
    out_awb.add_bytes(hca_bytes, 0)
    row["AwbFile"] = (row["AwbFile"][0], out_awb.save_bytes())
    row["Name"] = (row["Name"][0], cue_name)
    row["AcbGuid"] = (row["AcbGuid"][0], hashlib.md5(cue_name.encode()).digest())

    cn_name, cue_names = _parse_utf(row["CueNameTable"][1])
    cue_names[0]["CueName"] = (cue_names[0]["CueName"][0], cue_name)
    del cue_names[1:]
    row["CueNameTable"] = (row["CueNameTable"][0], _build_utf(cn_name, cue_names))

    ct_name, cues = _parse_utf(row["CueTable"][1])
    cues[0]["Length"] = (cues[0]["Length"][0], length_ms)
    del cues[1:]
    row["CueTable"] = (row["CueTable"][0], _build_utf(ct_name, cues))

    wf_name, waveforms = _parse_utf(row["WaveformTable"][1])
    waveforms[0]["NumSamples"] = (waveforms[0]["NumSamples"][0], num_samples)
    waveforms[0]["SamplingRate"] = (waveforms[0]["SamplingRate"][0], fmt.sample_rate)
    waveforms[0]["NumChannels"] = (waveforms[0]["NumChannels"][0], fmt.channel_count)
    row["WaveformTable"] = (row["WaveformTable"][0], _build_utf(wf_name, waveforms))

    # Re-serialize every remaining embedded @UTF sub-table so the whole ACB uses the
    # PyCriCodecsEx layout the game requires, not the template's original CRI layout.
    for col in list(row):
        typ, val = row[col]
        if (
            typ == 0xB
            and isinstance(val, (bytes, bytearray))
            and bytes(val[:4]) == b"@UTF"
        ):
            row[col] = (typ, _build_utf(*_parse_utf(bytes(val))))

    Path(dst).write_bytes(_build_utf(name, acb))


# @UTF (CRI UTF table) parser + serializer, reproducing PyCriCodecsEx byte for byte.
# The game's custom-chart loader only accepts this exact layout (per-column storage rule,
# string table, 8-byte padding); cricodecs' own serializer differs and is rejected.
# A value is (type_code, value): int for numbers, str for strings, bytes for data, None
# for name-only columns. type_code is the CRI type nibble (0xA string, 0xB data).
_UTF_NUMFMT = {
    0: "B",
    1: "b",
    2: "H",
    3: "h",
    4: "I",
    5: "i",
    6: "Q",
    7: "q",
    8: "f",
    9: "d",
}
_UTF_NUMSIZE = {0: 1, 1: 1, 2: 2, 3: 2, 4: 4, 5: 4, 6: 8, 7: 8, 8: 4, 9: 8}


def _parse_utf(data: bytes, encoding: str = "utf-8") -> tuple[str, list[dict]]:
    table_size = struct.unpack(">I", data[4:8])[0]
    rows_off = struct.unpack(">H", data[10:12])[0]
    str_off, data_off, name_off = struct.unpack(">III", data[12:24])
    ncols, row_width = struct.unpack(">HH", data[24:28])
    nrows = struct.unpack(">I", data[28:32])[0]
    strtab = data[8 + str_off : 8 + data_off]
    binary = data[8 + data_off : 8 + table_size]

    def cstr(off: int) -> bytes:
        return strtab[off : strtab.index(b"\x00", off)]

    def read(typ: int, buf: bytes, pos: int):
        if typ == 0xA:
            return cstr(struct.unpack(">I", buf[pos : pos + 4])[0]).decode(encoding), 4
        if typ == 0xB:
            off, size = struct.unpack(">II", buf[pos : pos + 8])
            return bytes(binary[off : off + size]), 8
        sz = _UTF_NUMSIZE[typ]
        return struct.unpack(">" + _UTF_NUMFMT[typ], buf[pos : pos + sz])[0], sz

    cols = []  # (storage, type, name, const_value)
    p = 0x20
    for _ in range(ncols):
        flag = data[p]
        storage, typ = flag & 0xF0, flag & 0x0F
        p += 1
        name = cstr(struct.unpack(">I", data[p : p + 4])[0]).decode(encoding)
        p += 4
        const = None
        if storage == 0x30:
            const, adv = read(typ, data, p)
            p += adv
        cols.append((storage, typ, name, const))

    dictarray = []
    for r in range(nrows):
        rowdict = {}
        rp = 8 + rows_off + r * row_width
        for storage, typ, nm, const in cols:
            if storage == 0x50:
                val, adv = read(typ, data, rp)
                rp += adv
            else:
                val = const if storage == 0x30 else None
            rowdict[nm] = (typ, val)
        dictarray.append(rowdict)
    return cstr(name_off).decode(encoding), dictarray


def _build_utf(
    table_name: str, dictarray: list[dict], encoding: str = "utf-8"
) -> bytes:
    first = dictarray[0]
    stflag = []  # (storage, type, name, const_or_None)
    for name, (typ, val) in first.items():
        if len(dictarray) != 1:
            if any(d[name][1] != val for d in dictarray):
                stflag.append((0x50, typ, name, None))
            elif val is None:
                stflag.append((0x10, typ, name, None))
            else:
                stflag.append((0x30, typ, name, val))
        elif val is None or val == "<NULL>":
            stflag.append((0x10, typ, name, None))
        else:
            stflag.append((0x50, typ, name, None))

    names: list[str] = []
    binary = b""
    for d in dictarray:
        for k in d:
            if k not in names:
                names.append(k)
    for d in dictarray:
        for _typ, val in d.values():
            if isinstance(val, str) and val not in names:
                names.append(val)
            if isinstance(val, (bytes, bytearray)) and val not in binary:
                binary += val
    names = [table_name] + names
    if "<NULL>" in names:
        names.remove("<NULL>")
        names = ["<NULL>"] + names
    strings = b"\x00".join(s.encode(encoding) for s in names) + b"\x00"

    def idx(enc: bytes) -> int:
        if enc == b"":
            return strings.index(b"\x00\x00") + 1
        return strings.index(b"\x00" + enc + b"\x00") + 1

    columns = bytearray()
    for storage, typ, name, const in stflag:
        columns += bytes((storage | typ,)) + struct.pack(
            ">I", idx(name.encode(encoding))
        )
        if storage == 0x30:
            if typ == 0xA:
                cenc = const.encode(encoding)
                columns += (
                    b"\x00\x00\x00\x00"
                    if strings.startswith(cenc + b"\x00")
                    else struct.pack(">I", idx(cenc))
                )
            elif typ == 0xB:
                columns += struct.pack(">II", binary.index(const), len(const))
            else:
                columns += struct.pack(">" + _UTF_NUMFMT[typ], const)

    rows = bytearray()
    for d in dictarray:
        for storage, typ, name, _const in stflag:
            if storage != 0x50:
                continue
            val = d[name][1]
            if typ == 0xA:
                rows += struct.pack(">I", idx(val.encode(encoding)))
            elif typ == 0xB:
                rows += struct.pack(">II", binary.index(val), len(val))
            else:
                rows += struct.pack(">" + _UTF_NUMFMT[typ], val)

    row_width = sum(
        struct.calcsize(
            ">II" if t == 0xB else (">I" if t == 0xA else ">" + _UTF_NUMFMT[t])
        )
        for s, t, _n, _c in stflag
        if s == 0x50
    )
    datalen = len(columns) + len(rows) + len(strings) + len(binary) + 0x18
    data_offset = datalen + ((8 - datalen % 8) % 8)
    binary_offset = data_offset if len(binary) == 0 else datalen - len(binary)
    tn_enc = table_name.encode(encoding)
    name_ptr = 0 if strings.startswith(tn_enc) else idx(tn_enc)
    out = bytearray(
        struct.pack(
            ">4sIBBHIIIHHI",
            b"@UTF",
            data_offset,
            0,
            0,
            len(columns) + 0x18,
            datalen - len(strings) - len(binary),
            binary_offset,
            name_ptr,
            len(stflag),
            row_width,
            len(dictarray),
        )
    )
    out += columns + rows + strings + binary
    if len(out) % 8 != 0:
        out = out[:8] + out[8:].ljust(data_offset, b"\x00")
    return bytes(out)


def _transcode_usm(dst: str, m1v: str) -> None:
    from cricodecs import usm

    usm.mux_to_file(dst, m1v)


def _compile_music(
    out_bundle: str,
    acb_path: str,
    bundle_name: str,
    cue_name: str,
    cue_type: str,
    template_bundle: str,
) -> None:
    import UnityPy
    from UnityPy.classes import AssetBundle, TextAsset

    acb_filename = f"{cue_name}.acb"
    with open(template_bundle, "rb") as f:
        env = UnityPy.load(f)
    new_acb = Path(acb_path).read_bytes()
    for obj in env.objects:
        if obj.type.name == "TextAsset":
            data: TextAsset = obj.read()
            data.m_Name = acb_filename
            data.m_Script = new_acb.decode("utf8", "surrogateescape")
            data.save()
        elif obj.type.name == "AssetBundle":
            bundle: AssetBundle = obj.read()
            bundle.m_Name = bundle.m_AssetBundleName = f"music/{cue_type}/{bundle_name}"
            for i, (name, asset) in enumerate(bundle.m_Container):
                bundle.m_Container[i] = (name.replace("0001_01", bundle_name), asset)
            bundle.save()
    for obj in env.objects:
        if obj.type.name == "MonoBehaviour":
            data = obj.read()
            if data.m_Name == "SoundBundleBuildData":
                for af in data.acbFiles:
                    af.assetBundleFileName = f"{acb_filename}.bytes"
                    af.cueSheetName = cue_name
                data.save()
    _write(out_bundle, _encrypt_ab(_save_as_unique_bundle(env.file)))


def _compile_jacket(
    out_bundle: str,
    image: str,
    output_name: str,
    template_bundle: str,
    extras: dict[str, str],
) -> None:
    import UnityPy
    from PIL import Image
    from UnityPy.classes import AssetBundle, Texture2D
    from UnityPy.enums import ClassIDType
    from UnityPy.files import SerializedFile

    with open(template_bundle, "rb") as f:
        env = UnityPy.load(f)
    src = next(o for o in env.objects if o.type == ClassIDType.Texture2D)
    sfile: SerializedFile = list(env.file.files.values())[0]
    ab: AssetBundle = next(
        o for o in env.objects if o.type == ClassIDType.AssetBundle
    ).read()
    tex: Texture2D = src.read()
    tex.m_Name = output_name
    tex.set_image(
        Image.open(image).convert("RGBA").resize((740, 740), Image.Resampling.LANCZOS)
    )
    tex.save()
    ab.m_AssetBundleName = ab.m_Name = f"music/jacket/{output_name}"
    for i, (name, asset) in enumerate(ab.m_Container):
        ab.m_Container[i] = (name.replace("jacket_s_001", output_name), asset)
    new_id = max(sfile.files)
    for extra_name, extra_image in extras.items():
        new_id += 1
        nxt = sfile.objects[new_id] = _copy_object_reader(src, new_id)
        nxt = nxt.read()
        nxt.m_Name = extra_name
        nxt.set_image(
            Image.open(extra_image)
            .convert("RGBA")
            .resize((740, 740), Image.Resampling.LANCZOS)
        )
        nxt.save()
        pptr = _copy_pptr(ab.m_PreloadTable[-1], 0, new_id)
        ab.m_PreloadTable.append(pptr)
        info = _copy_assetinfo(
            ab.m_Container[-1][1], pptr, len(ab.m_PreloadTable) - 1, 1
        )
        ab.m_Container.append(
            (
                f"assets/sekai/assetbundle/resources/startapp/music/jacket/{output_name}/{extra_name}.png",
                info,
            )
        )
    ab.save()
    _write(out_bundle, _encrypt_ab(_save_as_unique_bundle(env.file)))


def _prepare_scores(
    chart_by_diff: dict[str, str], first_diff: str, lenient: set[str]
) -> dict[str, str]:
    from rg_converters.core.bpm import Bpm
    from rg_converters.core.metadata import MetaData
    from rg_converters.core.score import Score
    from rg_converters.core.single import FeverChance, FeverStart, Skill
    from rg_converters.format import sus

    events = (Skill, FeverStart, FeverChance)

    texts: dict[str, str] = {}
    first_events: list = []
    first_bpms: list = []
    for diff, path in chart_by_diff.items():
        score = charts.load_score(path, strict=diff not in lenient)
        if diff == first_diff:
            first_events = [n for n in score.notes if isinstance(n, events)]
            first_bpms = [n for n in score.notes if isinstance(n, Bpm)]
        texts[diff] = charts.leveldata_text(
            score, from_sus=charts.chart_format(path) == "sus"
        )

    if not first_bpms:
        first_bpms = [Bpm(beat=0.0, bpm=120.0)]
    meta = MetaData(
        title="",
        artist="",
        designer="",
        waveoffset=0.0,
        requests=["ticks_per_beat 480"],
    )

    unshifted = sus.ExportOptions(skip_shift=True)
    info = Score(metadata=meta, notes=list(first_bpms) + list(first_events))
    texts["info"] = _normalize_sus(sus.dumps(info, unshifted)[0])

    chart_diffs = set(chart_by_diff)
    empty_score = Score(metadata=meta, notes=list(first_bpms))
    empty = _normalize_sus(sus.dumps(empty_score, unshifted)[0])
    for diff in ("easy", "normal", "hard", "expert", "master", "append"):
        if diff not in chart_diffs:
            texts[diff] = empty
    return texts


def _compile_music_scores(
    out_bundle: str, score_texts: dict[str, str], output_name: str, template_bundle: str
) -> None:
    import UnityPy
    from UnityPy.classes import AssetBundle, TextAsset
    from UnityPy.enums import ClassIDType
    from UnityPy.files import SerializedFile

    with open(template_bundle, "rb") as f:
        env = UnityPy.load(f)
    added: list[str] = []
    for obj in env.objects:
        if obj.type.name == "TextAsset":
            name = obj.peek_name()
            if name in score_texts:
                data = obj.read()
                data.m_Script = score_texts[name]
                data.save()
                added.append(name)
        elif obj.type.name == "AssetBundle":
            data: AssetBundle = obj.read()
            data.m_AssetBundleName = data.m_Name = f"music/music_score/{output_name}"
            for i, (name, asset) in enumerate(data.m_Container):
                data.m_Container[i] = (name.replace("0001_01", output_name), asset)
            data.save()
    for name, text in score_texts.items():
        if name in added:
            continue
        src = next(o for o in env.objects if o.type == ClassIDType.TextAsset)
        sfile: SerializedFile = list(env.file.files.values())[0]
        ab: AssetBundle = next(
            o for o in env.objects if o.type == ClassIDType.AssetBundle
        ).read()
        new_id = max(sfile.files) + 1
        nxt = sfile.objects[new_id] = _copy_object_reader(src, new_id)
        nxt: TextAsset = nxt.read()
        nxt.m_Name = name
        nxt.m_Script = text
        nxt.save()
        pptr = _copy_pptr(ab.m_PreloadTable[-1], 0, new_id)
        ab.m_PreloadTable.append(pptr)
        info = _copy_assetinfo(
            ab.m_Container[-1][1], pptr, len(ab.m_PreloadTable) - 1, 1
        )
        ab.m_Container.append(
            (
                f"assets/sekai/assetbundle/resources/startapp/music/music_score/{output_name}/{name}.txt",
                info,
            )
        )
        ab.save()
    _write(out_bundle, _encrypt_ab(_save_as_unique_bundle(env.file)))


_BPM = re.compile(r"^(#BPM[^:\s]*:)", re.IGNORECASE)
_MMM08 = re.compile(r"^(#\d{3}08:)(.*)", re.IGNORECASE)


def _normalize_sus(text: str) -> str:
    out = []
    for line in text.splitlines():
        m = _BPM.match(line)
        if m:
            line = m.group(1).upper() + line[len(m.group(1)) :]
        m = _MMM08.match(line)
        if m:
            line = m.group(1) + m.group(2).upper()
        out.append(line)
    return "\n".join(out) + "\n"


def _compile_mv(
    out_bundle: str, usm: str, bundle_name: str, mv_type: str, template_bundle: str
) -> None:
    import UnityPy
    from UnityPy.classes import AssetBundle, TextAsset

    template_id = "0006" if mv_type == "sekai_mv" else "0378"
    usm_filename = f"{bundle_name}.usm"
    with open(template_bundle, "rb") as f:
        env = UnityPy.load(f)
    new_usm = Path(usm).read_bytes()
    for obj in env.objects:
        if obj.type.name == "TextAsset":
            data: TextAsset = obj.read()
            data.m_Name = usm_filename
            data.m_Script = new_usm.decode("utf8", "surrogateescape")
            data.save()
        elif obj.type.name == "AssetBundle":
            data: AssetBundle = obj.read()
            data.m_Name = data.m_AssetBundleName = (
                f"live/2dmode/{mv_type}/{bundle_name}"
            )
            for i, (name, asset) in enumerate(data.m_Container):
                data.m_Container[i] = (name.replace(template_id, bundle_name), asset)
            data.save()
    for obj in env.objects:
        if obj.type.name == "MonoBehaviour":
            data = obj.read()
            if data.m_Name == "MovieBundleBuildData":
                for mb in data.movieBundleDatas:
                    mb.usmFileName = f"{usm_filename}.bytes"
                data.save()
    _write(out_bundle, _encrypt_ab(_save_as_unique_bundle(env.file)))


def _write(path: str, data: bytes) -> None:
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    with open(path, "wb") as f:
        f.write(data)


def compile_chart(data: dict, bases: str, out_base: str, log: Log = print) -> list[str]:
    import UnityPy

    UnityPy.config.FALLBACK_UNITY_VERSION = "2022.3.62f2"
    try:
        UnityPy.config.warnings.simplefilter(
            "ignore", UnityPy.exceptions.UnityVersionFallbackWarning
        )
    except Exception:
        pass

    info = data["info"]
    ident = str(info.get("identifier") or "").strip()
    if not ident:
        raise ValueError("ChartInfo is missing 'identifier', cannot build")
    offset_ms = int(info.get("offset_ms") or 0)
    mv_offset_ms = int(info.get("additional_mv_offset_ms") or 0)
    covers = [c for c in info.get("covers", []) if not c.get("default")]

    work = tempfile.mkdtemp(prefix="gs6enc_")
    try:
        wav_dir = Path(work) / "wav"
        wav_dir.mkdir(parents=True, exist_ok=True)

        def _cover_src(cid: int, kind: str) -> str | None:
            for src, zip_name in data.get("cover_files", []):
                if zip_name.startswith(f"covers/{kind}_{cid}."):
                    return src
            return None

        audio_jobs: list[tuple[str, str, str | None]] = [
            ("01", "long", data["track"]),
            ("01", "short", data["track_pre"]),
        ]
        for c in covers:
            cid = int(c["id"])
            suffix = f"{cid + 1:02d}"
            audio_jobs.append((suffix, "long", _cover_src(cid, "cover")))
            audio_jobs.append((suffix, "short", _cover_src(cid, "cover_pre")))

        acbs: dict[tuple[str, str], str] = {}
        for suffix, cue_type, src in audio_jobs:
            if not src:
                raise FileNotFoundError(
                    f"missing cover audio for suffix {suffix} ({cue_type})"
                )
            bundle_name = f"{ident}_{suffix}"
            cue_name = (
                bundle_name if cue_type == "long" else f"{bundle_name}_{cue_type}"
            )
            wav = (wav_dir / f"{bundle_name}_{cue_type}.wav").as_posix()
            log(f"ffmpeg wav: {bundle_name} {cue_type}")
            _transcode_wav(
                src,
                wav,
                offset_ms=offset_ms if cue_type == "long" else 0,
                silence_seconds=LEAD_SILENCE_SECONDS if cue_type == "long" else 0,
            )
            acb = (wav_dir / f"{bundle_name}_{cue_type}.acb").as_posix()
            log(f"ACB: {cue_name} ({cue_type})")
            _transcode_acb(
                acb, cue_name, wav, _bases(bases, "android")[f"acb_{cue_type}"]
            )
            acbs[(suffix, cue_type)] = acb

        usms: dict[str, str] = {}
        for mv_type, src in (("original_mv", data["ogmv"]), ("sekai_mv", data["2dmv"])):
            if not src:
                continue
            m1v = (Path(work) / f"{mv_type}.m1v").as_posix()
            log(f"ffmpeg m1v: {mv_type}")
            _transcode_m1v(src, m1v, offset_ms=offset_ms + mv_offset_ms)
            usm = (Path(work) / f"{mv_type}.usm").as_posix()
            log(f"USM: {mv_type}")
            _transcode_usm(usm, m1v)
            usms[mv_type] = usm

        jacket_extras: dict[str, str] = {}
        for c in covers:
            if c.get("has_jacket"):
                cid = int(c["id"])
                jk = _cover_src(cid, "cover_jacket")
                if not jk:
                    raise FileNotFoundError(
                        f"cover {cid} declares has_jacket but no cover_jacket file"
                    )
                jacket_extras[f"jacket_s_{ident}_an_custom_{cid:02d}"] = jk

        chart_by_diff = {d["difficulty"]: d["_path"] for d in data["diffs"]}
        lenient = {d["difficulty"] for d in data["diffs"] if d.get("_lenient")}
        log("preparing charts (skills/fever kept in each chart and copied to info)")
        score_texts = _prepare_scores(
            chart_by_diff, data["diffs"][0]["difficulty"], lenient
        )

        bundles_manifest: list[str] = []

        def add_bundle(name: str) -> None:
            if name not in bundles_manifest:
                bundles_manifest.append(name)

        for plat in PLATFORMS:
            bp = _bases(bases, plat)
            pdir = Path(work) / plat

            jacket_name = f"jacket_s_{ident}"
            jacket_out = (pdir / "music/jacket" / jacket_name).as_posix()
            log(f"[{plat}] jacket")
            _compile_jacket(
                jacket_out, data["jacket"], jacket_name, bp["jacket"], jacket_extras
            )
            add_bundle(f"music/jacket/{jacket_name}")

            score_name = f"{ident}_01"
            score_out = (pdir / "music/music_score" / score_name).as_posix()
            log(f"[{plat}] music_score")
            _compile_music_scores(
                score_out, score_texts, score_name, bp["music_scores"]
            )
            add_bundle(f"music/music_score/{score_name}")

            for (suffix, cue_type), acb in acbs.items():
                bundle_name = f"{ident}_{suffix}"
                cue_name = (
                    bundle_name if cue_type == "long" else f"{bundle_name}_{cue_type}"
                )
                out_file = (pdir / f"music/{cue_type}" / bundle_name).as_posix()
                log(f"[{plat}] music/{cue_type}/{bundle_name}")
                _compile_music(
                    out_file, acb, bundle_name, cue_name, cue_type, bp[cue_type]
                )
                add_bundle(f"music/{cue_type}/{bundle_name}")

            for mv_type, usm in usms.items():
                bundle_name = ident
                out_file = (pdir / f"live/2dmode/{mv_type}" / bundle_name).as_posix()
                log(f"[{plat}] live/2dmode/{mv_type}/{bundle_name}")
                _compile_mv(out_file, usm, bundle_name, mv_type, bp[mv_type])
                add_bundle(f"live/2dmode/{mv_type}/{bundle_name}")

        info_out = dict(info)
        info_out.pop("id", None)
        info_out["bundles"] = bundles_manifest
        outputs: list[str] = []
        for plat in PLATFORMS:
            out_path = f"{out_base}.{plat}.chart.gs6"
            log(f"packing {os.path.basename(out_path)}")
            info_plat = dict(info_out, platform=plat)
            with zipfile.ZipFile(out_path, "w", zipfile.ZIP_DEFLATED) as z:
                z.writestr(
                    "ChartInfo.json",
                    json.dumps(info_plat, ensure_ascii=False, indent=2),
                )
                z.writestr("platform", plat)
                for name in bundles_manifest:
                    z.write((Path(work) / plat / name).as_posix(), name)
            outputs.append(out_path)
        log("done")
        return outputs
    finally:
        shutil.rmtree(work, ignore_errors=True)
