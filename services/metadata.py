"""NovelAI metadata parsing and presentation helpers."""

import html
import json
import re
import struct
import zlib


def _png_text_chunks(data: bytes) -> list[str]:
    if not data.startswith(b"\x89PNG\r\n\x1a\n"):
        return []
    texts: list[str] = []
    pos = 8
    while pos + 12 <= len(data):
        length = struct.unpack(">I", data[pos:pos + 4])[0]
        kind = data[pos + 4:pos + 8]
        chunk_start = pos + 8
        chunk_end = chunk_start + length
        crc_end = chunk_end + 4
        if chunk_end > len(data) or crc_end > len(data):
            break
        chunk = data[chunk_start:chunk_end]
        if kind == b"tEXt":
            try:
                texts.append(chunk.decode("latin-1"))
            except UnicodeDecodeError:
                pass
        elif kind == b"iTXt":
            parts = chunk.split(b"\x00", 5)
            if len(parts) == 6:
                compressed_flag = parts[1]
                text_bytes = parts[5]
                if compressed_flag == b"\x01":
                    try:
                        text_bytes = zlib.decompress(text_bytes)
                    except zlib.error:
                        text_bytes = b""
                if text_bytes:
                    try:
                        texts.append(text_bytes.decode("utf-8"))
                    except UnicodeDecodeError:
                        pass
        elif kind == b"zTXt":
            parts = chunk.split(b"\x00", 1)
            if len(parts) == 2:
                try:
                    texts.append(zlib.decompress(parts[1][1:]).decode("latin-1"))
                except (zlib.error, UnicodeDecodeError):
                    pass
        pos = crc_end
        if kind == b"IEND":
            break
    return texts


def parse_nai_metadata(data: bytes) -> dict:
    texts = _png_text_chunks(data)
    blob = "\n".join(t for t in texts if t)
    found = {}
    candidates = []
    for start, ch in enumerate(blob):
        if ch != "{":
            continue
        depth = 0
        for pos in range(start, min(len(blob), start + 200_000)):
            if blob[pos] == "{":
                depth += 1
            elif blob[pos] == "}":
                depth -= 1
                if depth == 0:
                    candidate = blob[start:pos + 1]
                    if re.search(r"prompt|uc|sampler|seed|steps|scale|width|height", candidate, re.I):
                        candidates.append(candidate)
                    break
    for candidate in candidates:
        try:
            obj = json.loads(candidate)
        except json.JSONDecodeError:
            continue
        if isinstance(obj, dict):
            found.update(obj)
            params = obj.get("parameters")
            if isinstance(params, dict):
                found.update(params)
    aliases = {
        "prompt": ["prompt", "Description"],
        "negative_prompt": ["negative_prompt", "negative prompt", "uc", "Undesired Content"],
        "model": ["model", "Model"],
        "width": ["width"],
        "height": ["height"],
        "steps": ["steps"],
        "scale": ["scale", "guidance"],
        "seed": ["seed"],
        "sampler": ["sampler"],
        "ucPreset": ["ucPreset", "uc_preset"],
        "uc_preset": ["ucPreset", "uc_preset"],
        "noise_schedule": ["noise_schedule", "noiseSchedule"],
        "cfg_rescale": ["cfg_rescale", "cfgRescale"],
        "qualityToggle": ["qualityToggle", "quality_toggle"],
        "variety_plus": ["variety_plus", "varietyPlus"],
        "dynamic_thresholding": ["dynamic_thresholding", "dynamicThresholding"],
        "n_samples": ["n_samples", "nSamples"],
        "params_version": ["params_version", "paramsVersion"],
        "v4_prompt": ["v4_prompt"],
        "v4_negative_prompt": ["v4_negative_prompt"],
    }
    meta = {}
    for target, keys in aliases.items():
        for key in keys:
            if key in found and found[key] not in ("", None):
                meta[target] = found[key]
                break
    for target, pattern in {
        "prompt": r"(?:prompt|description)[:=]\s*([^\n\r]+)",
        "negative_prompt": r"(?:negative prompt|uc|undesired content)[:=]\s*([^\n\r]+)",
        "model": r"model[:=]\s*([^\n\r,]+)",
        "sampler": r"sampler[:=]\s*([^\n\r,]+)",
    }.items():
        if target not in meta:
            m = re.search(pattern, blob, re.I)
            if m:
                meta[target] = m.group(1).strip()
    for target, pattern in {
        "width": r"width[:=]\s*(\d+)",
        "height": r"height[:=]\s*(\d+)",
        "steps": r"steps[:=]\s*(\d+)",
        "scale": r"(?:scale|guidance)[:=]\s*([0-9.]+)",
        "cfg_rescale": r"(?:cfg_rescale|cfg rescale)[:=]\s*([0-9.]+)",
        "seed": r"seed[:=]\s*(\d+)",
    }.items():
        if target not in meta:
            m = re.search(pattern, blob, re.I)
            if m:
                meta[target] = m.group(1)
    return meta


def metadata_summary(meta: dict) -> str:
    if not meta:
        return "📭 NovelAI metadata не найдена. Можно попробовать отправить оригинальный PNG/WebP/JPEG как файл."
    labels = {"prompt": "Prompt", "negative_prompt": "UC/негатив", "model": "Model", "width": "Width", "height": "Height", "steps": "Steps", "scale": "Guidance", "cfg_rescale": "CFG rescale", "seed": "Seed", "sampler": "Sampler", "uc_preset": "UC preset", "noise_schedule": "Noise"}
    lines = ["📦 <b>Нашла metadata</b>"]
    for key, label in labels.items():
        if key in meta:
            lines.append(f"<b>{label}:</b> <code>{html.escape(str(meta[key])[:900])}</code>")
    return "\n".join(lines)


def metadata_settings_summary(meta: dict) -> str:
    if not meta:
        return "📭 Metadata settings не найдены."
    keys = ["model", "width", "height", "steps", "scale", "cfg_rescale", "sampler", "noise_schedule", "seed", "ucPreset", "uc_preset", "qualityToggle", "variety_plus", "n_samples", "params_version", "negative_prompt"]
    lines = ["📋 <b>Настройки metadata</b>"]
    for key in keys:
        if key in meta:
            lines.append(f"<b>{html.escape(key)}:</b> <code>{html.escape(str(meta[key])[:900])}</code>")
    return "\n".join(lines)


COMPARE_FIELDS = [
    "model", "width", "height", "steps", "scale", "cfg_rescale", "sampler",
    "noise_schedule", "seed", "ucPreset", "qualityToggle", "variety_plus",
    "dynamic_thresholding", "n_samples", "params_version",
    "v4_prompt.use_order", "v4_prompt.use_coords",
    "v4_negative_prompt.use_order", "v4_negative_prompt.use_coords",
    "v4_negative_prompt.legacy_uc",
]
_METADATA_ALIASES = {"ucPreset": ("ucPreset", "uc_preset"), "qualityToggle": ("qualityToggle", "quality_toggle")}


def _nested_get(data: dict, dotted: str):
    current = data
    for part in dotted.split("."):
        if not isinstance(current, dict) or part not in current:
            return None
        current = current[part]
    return current


def _payload_compare_value(payload: dict, field: str):
    if field == "model":
        return payload.get("model")
    parameters = payload.get("parameters", {}) if isinstance(payload, dict) else {}
    return _nested_get(parameters, field)


def _metadata_compare_value(meta: dict, field: str):
    for key in _METADATA_ALIASES.get(field, (field,)):
        value = _nested_get(meta, key) if "." in key else meta.get(key)
        if value is not None:
            return value
    return None


def _norm_compare_value(value):
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return float(value)
    if value is None:
        return None
    text = str(value).strip()
    if text.lower() in {"true", "false"}:
        return text.lower() == "true"
    try:
        return float(text)
    except ValueError:
        return text


def _compare_status(site_value, bot_value) -> str:
    if site_value is None and bot_value is None:
        return "—"
    if site_value is None or bot_value is None:
        return "❌"
    return "✅" if _norm_compare_value(site_value) == _norm_compare_value(bot_value) else "❌"


def _format_compare_value(value) -> str:
    if value is None:
        return "—"
    text = json.dumps(value, ensure_ascii=False) if isinstance(value, (dict, list)) else str(value)
    return text[:77] + "…" if len(text) > 80 else text


def nai_compare_summary_text(meta: dict, payload: dict) -> str:
    rows = ["field | website metadata | bot payload | status"]
    for field in COMPARE_FIELDS:
        rows.append(f"{field} | {_format_compare_value(_metadata_compare_value(meta, field))} | {_format_compare_value(_payload_compare_value(payload, field))} | {_compare_status(_metadata_compare_value(meta, field), _payload_compare_value(payload, field))}")
    return "⚖️ <b>NovelAI website-vs-bot payload compare</b>\n<pre>" + html.escape("\n".join(rows)) + "</pre>"
