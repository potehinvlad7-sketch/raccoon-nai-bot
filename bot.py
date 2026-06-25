import asyncio
import logging
import os
import html
import json
import re
import struct
from io import BytesIO
from pathlib import Path
from datetime import datetime, timezone, timedelta

from aiogram import Bot, Dispatcher, F, types
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import BufferedInputFile, FSInputFile
from aiogram.client.session.aiohttp import AiohttpSession
from dotenv import load_dotenv

from config_defaults import QUICK_PRESETS, RESOLUTIONS, MODELS, SAMPLERS, UC_PRESETS, NOISE_SCHEDULES, UserSettings, AELITA_DESCRIPTION
from keyboards import (
    main_menu as base_main_menu, settings_menu, modes_menu, presets_menu, pending_prompt_menu,
    after_generation_menu, generation_item_menu, artraccoon_menu, meta_import_menu, confirm_reset_menu, model_menu, size_menu, sampler_menu, uc_menu, noise_menu, seed_menu, samples_menu, moderation_dictionary_menu, dictionary_menu, dictionary_pending_menu
)
from app.services.nai_client import (
    NovelAIClient, NovelAIError, payload_summary, sanitize_payload,
    SITE_MODE_STEPS, SITE_MODE_SCALE, SITE_MODE_CFG_RESCALE, SITE_MODE_SAMPLER, SITE_MODE_NOISE_SCHEDULE,
)
from prompt_tools import (
    DICTIONARY_PATH, add_learned_mapping, has_unknown_russian, learn_from_english_prompt,
    load_learned_dictionary, natural_to_nai_tags, parse_english_tags, reject_tags,
    looks_like_english_tags, save_learned_dictionary
)
from storage import (
    get_settings, save_settings, patch_settings, add_history, get_history,
    add_favorite, get_favorites, delete_favorite, set_last_metadata, get_last_metadata,
    set_last_payload, get_last_payload
)

load_dotenv()

BOT_TOKEN = os.getenv("BOT_TOKEN", "")
NOVELAI_TOKEN = (os.getenv("NOVELAI_TOKEN") or os.getenv("NAI_TOKEN") or "").strip()
NAI_MODEL = os.getenv("NAI_MODEL", "").strip()
PROXY_URL = os.getenv("PROXY_URL", "socks5://127.0.0.1:1080").strip()
CHANNEL_URL = os.getenv("CHANNEL_URL", "").strip()

ADMIN_IDS = [
    int(x.strip())
    for x in os.getenv("ADMIN_IDS", "").split(",")
    if x.strip().isdigit()
]

logging.basicConfig(level=logging.INFO)
log = logging.getLogger("novelai_tg_bot")

bot: Bot | None = None
dp = Dispatcher()
nai = NovelAIClient(NOVELAI_TOKEN, default_model=NAI_MODEL, proxy_url=PROXY_URL)

class GenState(StatesGroup):
    waiting_prompt = State()
    waiting_ar_base = State()
    waiting_ar_base_uc = State()
    waiting_ar_char_neg = State()
    waiting_setting = State()
    waiting_dict_ru = State()
    waiting_dict_tags = State()
    waiting_dict_review_ru = State()

TMP_DIR = Path("data/tmp_images")
TMP_DIR.mkdir(parents=True, exist_ok=True)
GENERATED_DIR = Path("data/generated")
GENERATED_DIR.mkdir(parents=True, exist_ok=True)
# TODO: add safe generated-image cleanup by age and total storage size when retention policy is defined.

def main_menu():
    return base_main_menu(CHANNEL_URL)

SAFE_RESOLUTIONS = {(512, 768), (768, 1344), (832, 1216), (1024, 1024), (1216, 832)}
ANLAS_WARNING = "💎 Эта функция временно отключена."
DAILY_GENERATION_LIMIT = 10
NON_ADMIN_COOLDOWN_SECONDS = 60
GENERATION_TIMEOUT_SECONDS = 180
generation_lock = asyncio.Lock()
generation_waiting = 0
moderation_candidates: dict[str, list[str]] = {}
SETTING_PROMPTS = {
    "size": "📐 Пришли размер, например <code>832x1216</code>.",
    "steps": "👣 Пришли количество шагов, например <code>28</code>.",
    "scale": "🧲 Пришли CFG / силу промта, например <code>7.5</code>.",
    "seed": "🎲 Пришли seed числом или <code>random</code>.",
    "negative": "🚫 Пришли negative prompt. Чтобы очистить — отправь <code>-</code>.",
    "model": "🧠 Пришли название модели: <code>" + "</code>, <code>".join(MODELS) + "</code>.",
    "sampler": "🎛 Пришли sampler: <code>" + "</code>, <code>".join(SAMPLERS) + "</code>.",
    "n": "🖼 Пришли количество картинок: <code>1</code>, <code>2</code>, <code>3</code> или <code>4</code>.",
    "uc": "🧪 Пришли UC-пресет: <code>" + "</code>, <code>".join(UC_PRESETS) + "</code>.",
    "cfg": "♻️ Пришли CFG rescale от 0 до 1, например <code>0.4</code>.",
    "noise": "🌊 Пришли noise schedule: <code>" + "</code>, <code>".join(NOISE_SCHEDULES) + "</code>.",
    "img2img": "📎 Пришли силу Img2Img в формате <code>0.55/0.10</code>.",
}

def assemble_ar_prompt(s, character_prompt: str) -> str:
    return ", ".join(part.strip() for part in [s.artraccoon_base_prompt, character_prompt] if part.strip())

def ar_payload_mode(s) -> str:
    if s.artraccoon_force_concat:
        return "fallback concat (forced)"
    model = NAI_MODEL or MODELS.get(s.model_name, "")
    return "Character Payload for v4/v4.5" if model.startswith(("nai-diffusion-4", "nai-diffusion-4-5")) else "fallback concat"

def parse_nai_metadata(data: bytes) -> dict:
    texts = []
    if data.startswith(b"\x89PNG\r\n\x1a\n"):
        pos = 8
        while pos + 8 <= len(data):
            length = struct.unpack(">I", data[pos:pos + 4])[0]
            kind = data[pos + 4:pos + 8]
            chunk = data[pos + 8:pos + 8 + length]
            if kind in {b"tEXt", b"iTXt", b"zTXt"}:
                texts.append(chunk.decode("utf-8", "ignore"))
            pos += 12 + length
    texts.append(data[:2_000_000].decode("utf-8", "ignore"))
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

def _safe_generated_image_path(user_id: int, timestamp: str, idx: int) -> Path:
    safe_timestamp = re.sub(r"[^0-9A-Za-z_.-]+", "_", timestamp)
    filename = f"{int(user_id)}_{safe_timestamp}_{int(idx)}.png"
    path = GENERATED_DIR / filename
    resolved_dir = GENERATED_DIR.resolve()
    resolved_path = path.resolve()
    if resolved_dir not in resolved_path.parents:
        raise ValueError("Unsafe generated image path")
    return path

def _save_generated_images(user_id: int, timestamp: str, images: list[bytes]) -> list[dict]:
    saved = []
    for idx, img in enumerate(images, start=1):
        path = _safe_generated_image_path(user_id, timestamp, idx)
        path.write_bytes(img)
        saved.append({"path": path.as_posix(), "filename": f"novelai_{idx}.png", "index": idx})
    return saved

def _safe_existing_generated_path(raw_path: str) -> Path | None:
    if not raw_path:
        return None
    path = Path(raw_path)
    if path.is_absolute() or ".." in path.parts:
        return None
    try:
        resolved_path = path.resolve()
        resolved_dir = GENERATED_DIR.resolve()
    except OSError:
        return None
    if resolved_dir not in resolved_path.parents:
        return None
    return path if path.exists() and path.is_file() else None

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

_METADATA_ALIASES = {
    "ucPreset": ("ucPreset", "uc_preset"),
    "qualityToggle": ("qualityToggle", "quality_toggle"),
}


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
        if "." in key:
            value = _nested_get(meta, key)
        else:
            value = meta.get(key)
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
    if len(text) > 80:
        text = text[:77] + "…"
    return text


def nai_compare_summary_text(meta: dict, payload: dict) -> str:
    rows = ["field | website metadata | bot payload | status"]
    for field in COMPARE_FIELDS:
        site_value = _metadata_compare_value(meta, field)
        bot_value = _payload_compare_value(payload, field)
        rows.append(
            f"{field} | {_format_compare_value(site_value)} | {_format_compare_value(bot_value)} | {_compare_status(site_value, bot_value)}"
        )
    return (
        "⚖️ <b>NovelAI website-vs-bot payload compare</b>\n"
        + "<pre>"
        + html.escape("\n".join(rows))
        + "</pre>"
    )

def nai_payload_summary_text(payload: dict, settings) -> str:
    summary = payload_summary(payload, settings)
    lines = ["🧪 <b>NovelAI payload summary</b>"]
    for key, value in summary.items():
        lines.append(f"<b>{html.escape(str(key))}:</b> <code>{html.escape(str(value))[:1200]}</code>")
    return "\n".join(lines)

def art_prompt_preview_text(s) -> str:
    character = s.artraccoon_character_prompt or s.pending_original_prompt or s.pending_prompt
    base = s.artraccoon_base_prompt or ""
    return (
        "🦝 <b>ArtRaccoon сборка. Запускаем?</b>\n\n"
        f"<b>Base Prompt:</b> {'saved' if base else 'empty'}, length <code>{len(base)}</code>\n"
        f"<code>{html.escape((base or '—')[:1200])}</code>\n\n"
        f"<b>Character Prompt:</b> length <code>{len(character)}</code>\n"
        f"<code>{html.escape((character or '—')[:1200])}</code>\n\n"
        f"<b>Mode:</b> <code>{ar_payload_mode(s)}</code>"
    )

def generation_settings_summary(s) -> str:
    negative = (s.negative_prompt or "").strip()
    negative = "empty" if not negative else html.escape(negative[:120])
    seed = "random" if s.seed == -1 else str(s.seed)
    return (
        f"📐 Размер: <code>{s.width}x{s.height}</code>\n"
        f"👣 Шаги: <code>{s.steps}</code>\n"
        f"🧲 CFG: <code>{s.scale}</code>\n"
        f"🎲 Seed: <code>{seed}</code>\n"
        f"🚫 Негатив: <code>{negative}</code>\n"
        f"🧠 Модель: <code>{html.escape(s.model_name)}</code>"
    )

def prompt_preview_text(prompt: str, original: str = "", settings=None, remaining: int | None = None) -> str:
    remaining_line = f"\n\nСегодня осталось: {remaining}/{DAILY_GENERATION_LIMIT}" if remaining is not None else ""
    warning_line = "\n\n⚠️ Проверь перевод." if original and has_unknown_russian(original) else ""
    if original and original.strip() and original.strip() != prompt.strip():
        return (
            "📝 <b>Промт готов. Запускаем?</b>\n\n"
            "<b>Исходник:</b>\n"
            f"<code>{html.escape(original[:1400])}</code>\n\n"
            "<b>Теговый промт:</b>\n"
            f"<code>{html.escape(prompt[:3000])}</code>"
            + warning_line
            + ("\n\n" + generation_settings_summary(settings) if settings else "")
            + remaining_line
        )
    return (
        "📝 <b>Промт готов. Запускаем?</b>\n\n"
        f"<code>{html.escape(prompt[:3000])}</code>"
        + warning_line
        + ("\n\n" + generation_settings_summary(settings) if settings else "")
        + remaining_line
    )

def apply_anlas_safe_defaults(user_id: int):
    s = get_settings(user_id)
    if s.pro_mode and user_id in ADMIN_IDS:
        return s
    updates = {}
    if s.n_samples != 1:
        updates["n_samples"] = 1
    if s.steps > 28:
        updates["steps"] = 28
    if (s.width, s.height) not in SAFE_RESOLUTIONS:
        updates.update({"width": 832, "height": 1216})
    if updates:
        s = patch_settings(user_id, **updates)
    return s

def safe_generation_defaults() -> dict:
    defaults = UserSettings()
    return {
        "width": defaults.width,
        "height": defaults.height,
        "steps": defaults.steps,
        "scale": defaults.scale,
        "seed": defaults.seed,
        "negative_prompt": defaults.negative_prompt,
        "model_name": defaults.model_name,
        "sampler": defaults.sampler,
        "n_samples": 1,
        "uc_preset": defaults.uc_preset,
        "cfg_rescale": defaults.cfg_rescale,
        "noise_schedule": defaults.noise_schedule,
        "variety_plus": defaults.variety_plus,
        "img2img_strength": defaults.img2img_strength,
        "img2img_noise": defaults.img2img_noise,
        "pro_mode": False,
        "nai_site_mode": False,
    }

def artraccoon_prompt_defaults() -> dict:
    return {
        "artraccoon_base_prompt": "",
        "artraccoon_base_uc": "",
        "artraccoon_character_prompt": "",
        "artraccoon_character_uc": "",
        "artraccoon_character_negative": "",
        "artraccoon_character_position": "",
    }

def today_key() -> str:
    return datetime.now(timezone.utc).date().isoformat()

def daily_count_for(s) -> int:
    return int(s.daily_generation_count or 0) if s.daily_generation_date == today_key() else 0

def remaining_generations(user_id: int) -> int | None:
    if user_id in ADMIN_IDS:
        return None
    return max(0, DAILY_GENERATION_LIMIT - daily_count_for(get_settings(user_id)))

def mark_generation_started(user_id: int) -> None:
    s = get_settings(user_id)
    updates = {"last_generation_started_at": datetime.now(timezone.utc).isoformat()}
    if user_id not in ADMIN_IDS:
        count = daily_count_for(s)
        updates.update({"daily_generation_date": today_key(), "daily_generation_count": count + 1})
    patch_settings(user_id, **updates)

def cooldown_remaining(user_id: int) -> int:
    if user_id in ADMIN_IDS:
        return 0
    raw = get_settings(user_id).last_generation_started_at
    if not raw:
        return 0
    try:
        started = datetime.fromisoformat(raw)
    except ValueError:
        return 0
    elapsed = (datetime.now(timezone.utc) - started).total_seconds()
    return max(0, int(NON_ADMIN_COOLDOWN_SECONDS - elapsed))

def user_label(user: types.User) -> str:
    parts = [f"id={user.id}"]
    if user.username:
        parts.append(f"@{user.username}")
    name = " ".join(x for x in [user.first_name, user.last_name] if x)
    if name:
        parts.append(name)
    return " / ".join(parts)

def _blockquote(text: str, limit: int = 3500) -> str:
    safe = html.escape((text or "—")[:limit])
    expandable = " expandable" if len(text or "") > 900 else ""
    return f"<blockquote{expandable}>{safe}</blockquote>"

def moderation_summary(user: types.User, original_prompt: str, final_prompt: str, s, candidates: list[str] | None = None) -> str:
    settings = (
        f"model={html.escape(s.model_name)}, size={s.width}x{s.height}, "
        f"steps={s.steps}, cfg={s.scale}, seed={'random' if s.seed == -1 else s.seed}, "
        f"sampler={html.escape(s.sampler)}, n={s.n_samples}"
    )
    candidates_block = ""
    if candidates:
        lines = "\n".join(f"• {html.escape(tag)}" for tag in candidates)
        candidates_block = f"\n\n📚 <b>New dictionary candidates</b>\n{lines}"
    return (
        "🛡 <b>Модерация генерации</b>\n"
        f"👤 <b>User:</b> <code>{html.escape(user_label(user))}</code>\n"
        f"⚙️ <b>Settings:</b> <code>{settings}</code>\n\n"
        "📝 <b>Prompt</b>\n"
        f"{_blockquote(original_prompt or final_prompt)}\n"
        "🎨 <b>Final Prompt</b>\n"
        f"{_blockquote(final_prompt)}\n"
        "🚫 <b>Negative prompt</b>\n"
        f"{_blockquote(s.negative_prompt or '—', 1800)}"
        f"{candidates_block}"
    )

async def send_moderation_copy(bot_obj: Bot, user: types.User, original_prompt: str, final_prompt: str, s, candidates: list[str] | None = None) -> None:
    if user.id in ADMIN_IDS:
        return
    text = moderation_summary(user, original_prompt, final_prompt, s, candidates)
    token = f"{user.id}:{int(datetime.now(timezone.utc).timestamp())}"
    if candidates:
        moderation_candidates[token] = list(dict.fromkeys(candidates))
    markup = moderation_dictionary_menu(token) if candidates else None
    for admin_id in ADMIN_IDS:
        try:
            await bot_obj.send_message(admin_id, text, parse_mode="HTML", reply_markup=markup)
        except Exception:
            log.exception("Failed to send moderation prompt summary to admin %s", admin_id)

async def send_moderation_image(bot_obj: Bot, user: types.User, img: bytes, original_prompt: str, final_prompt: str, s, idx: int, candidates: list[str] | None = None) -> None:
    if user.id in ADMIN_IDS:
        return
    caption = f"🛡 <b>Готовое изображение</b>\n👤 <code>{html.escape(user_label(user))}</code>\n📐 <code>{s.width}x{s.height}</code> · 🎲 <code>{'random' if s.seed == -1 else s.seed}</code>"
    if candidates:
        caption += "\n\n📚 <b>Dictionary candidates</b>\n" + "\n".join(f"• {html.escape(tag)}" for tag in candidates[:20])
    for admin_id in ADMIN_IDS:
        try:
            await bot_obj.send_photo(
                admin_id,
                BufferedInputFile(img, filename=f"moderation_{user.id}_{idx}.png"),
                caption=caption,
                parse_mode="HTML",
            )
        except Exception:
            log.exception("Failed to send generated image copy to admin %s for user %s image %s", admin_id, user.id, idx)

async def show_pending_prompt(message: types.Message, user_id: int) -> None:
    s = get_settings(user_id)
    if not s.pending_prompt:
        await message.answer("📝 Черновик пуст. Пришли новый промт обычным сообщением.", reply_markup=main_menu())
        return
    preview = art_prompt_preview_text(s) if s.artraccoon_mode else prompt_preview_text(s.pending_prompt, s.pending_original_prompt, s, remaining_generations(user_id))
    await message.answer(
        preview,
        parse_mode="HTML",
        reply_markup=pending_prompt_menu(bool(s.pending_image_path), (s.pro_mode and user_id in ADMIN_IDS) or s.artraccoon_mode, compact=s.artraccoon_mode),
    )


def howto_text(user_id: int | None = None) -> str:
    remaining = remaining_generations(user_id) if user_id is not None else None
    remaining_line = f"\n\nСегодня осталось: {remaining}/{DAILY_GENERATION_LIMIT}." if remaining is not None else ""
    return (
        "📘 <b>Инструкция</b>\n\n"
        "1. Просто отправь текст с идеей картинки.\n"
        "2. Бот покажет черновик и настройки.\n"
        "3. Нажми ✅ Генерировать.\n"
        "4. Лимит: 10 генераций в сутки.\n"
        "5. Между генерациями есть пауза 60 секунд.\n"
        "6. История: /history\n"
        "7. Избранное: /favorites\n"
        "8. Настройки: /settings или /xxx\n"
        "9. PRO-функции временно отключены. 🦝"
        + remaining_line
    )

def settings_text(user_id: int) -> str:
    s = get_settings(user_id)
    return (
        "⚙️ <b>Текущие настройки</b>\n\n"
        f"Модель: <code>{s.model_name}</code>\n"
        f"Размер: <code>{s.width}x{s.height}</code>\n"
        f"Режим: <code>{'ArtRaccoon' if s.artraccoon_mode else ('PRO' if s.pro_mode else 'Обычный')}</code>\n"
        f"Картинок: <code>{s.n_samples}</code>\n"
        f"Steps: <code>{s.steps}</code>\n"
        f"Guidance: <code>{s.scale}</code>\n"
        f"Sampler: <code>{s.sampler}</code>\n"
        f"Seed: <code>{s.seed}</code>\n"
        f"UC preset: <code>{s.uc_preset}</code>\n"
        f"Negative: <code>{s.negative_prompt or '—'}</code>\n"
        f"Furry: <code>{s.furry_mode}</code>\n"
        f"Background: <code>{s.background_mode}</code>\n"
        f"Quality tags: <code>{s.add_quality_tags}</code>\n"
        + (f"Variety+: <code>{s.variety_plus}</code>\n" if user_id in ADMIN_IDS or s.pro_mode or s.artraccoon_mode else "")
        + f"CFG rescale: <code>{s.cfg_rescale}</code>\n"
        f"Noise schedule: <code>{s.noise_schedule}</code>\n"
        f"Img2Img: <code>{s.img2img_strength} / {s.img2img_noise}</code>\n"
        f"Промт переведён из русского: <code>{bool(s.pending_original_prompt and s.pending_original_prompt != s.pending_prompt)}</code>"
    )


def settings_markup_for(user_id: int):
    s = get_settings(user_id)
    return settings_menu((s.pro_mode and user_id in ADMIN_IDS) or s.artraccoon_mode, show_pro_button=user_id in ADMIN_IDS)

def prompt_menu_for(s, user_id: int):
    return pending_prompt_menu(bool(s.pending_image_path), (s.pro_mode and user_id in ADMIN_IDS) or s.artraccoon_mode, compact=s.artraccoon_mode)

def prepare_prompt_for_user(user_id: int, text: str, force_tags: bool = False) -> tuple[str, str]:
    s = get_settings(user_id)
    if s.artraccoon_mode:
        character = natural_to_nai_tags(text) if force_tags else text
        return s.artraccoon_base_prompt, character
    converted = natural_to_nai_tags(text)
    original = "" if converted == text and looks_like_english_tags(text) else text
    return converted, original

def presets_text() -> str:
    lines = [
        "⚡ <b>Быстрые пресеты</b>",
        "",
        "▶️ — сразу сгенерировать.",
        "✍️ — показать промт, чтобы скопировать или дописать.",
        "",
        "Доступные идеи:",
    ]
    for preset in QUICK_PRESETS.values():
        lines.append(f"• <b>{preset['title']}</b>")
    return "\n".join(lines)

async def send_last_prompt(message: types.Message, actor: types.User | None = None) -> None:
    user = actor or message.from_user
    if user is None:
        await message.answer("Не вижу пользователя. Попробуй ещё раз.")
        return
    s = get_settings(user.id)
    if not s.last_prompt:
        await message.answer(
            "📝 У тебя пока нет последнего промта. Нажми 🎨 Новый промт или выбери ⚡ пресет.",
            reply_markup=main_menu(),
        )
        return
    await message.answer(
        "📝 <b>Последний промт</b>\n"
        f"<code>{html.escape(s.last_prompt)}</code>\n\n"
        f"🎲 Seed: <code>{s.seed}</code>\n"
        "Чтобы повторить генерацию, нажми /retry.",
        parse_mode="HTML",
        reply_markup=main_menu(),
    )

async def retry_last_prompt(message: types.Message, actor: types.User | None = None) -> None:
    user = actor or message.from_user
    if user is None:
        await message.answer("Не вижу пользователя. Попробуй ещё раз.")
        return
    s = get_settings(user.id)
    if not s.last_prompt:
        await message.answer(
            "🔁 Повторить пока нечего 😅 Сначала сгенерируй картинку через /gen или пресет.",
            reply_markup=main_menu(),
        )
        return
    await message.answer("🔁 Повторяю последний промт с текущими настройками.")
    await generate_image_from_prompt(message, s.last_prompt, actor=user)

@dp.message(Command("start"))
async def start(message: types.Message):
    get_settings(message.from_user.id)
    admin_line = "\n\nАдмин-панель и специальные команды доступны как раньше." if message.from_user.id in ADMIN_IDS else ""
    await message.answer(
        "🦝 <b>Привет! Я NovelAI bot</b>\n\n"
        "Напиши идею картинки обычным сообщением — я покажу черновик и кнопку генерации.\n"
        f"Сегодня осталось: <b>{remaining_generations(message.from_user.id)}/{DAILY_GENERATION_LIMIT}</b>."
        + admin_line,
        reply_markup=main_menu(),
        parse_mode="HTML",
    )

@dp.message(Command("help", "howto"))
async def help_cmd(message: types.Message):
    await message.answer(howto_text(message.from_user.id), reply_markup=main_menu(), parse_mode="HTML")

@dp.message(Command("xxx"))
async def xxx_cmd(message: types.Message):
    s = get_settings(message.from_user.id)
    pro_ui = (s.pro_mode and message.from_user.id in ADMIN_IDS) or s.artraccoon_mode
    await message.answer(settings_text(message.from_user.id), reply_markup=settings_markup_for(message.from_user.id), parse_mode="HTML")

@dp.message(Command("meta"))
async def meta_cmd(message: types.Message):
    target = message.reply_to_message or message
    file_id = None
    if target.document:
        file_id = target.document.file_id
    elif target.photo:
        file_id = target.photo[-1].file_id
    if not file_id:
        await message.answer("📦 Ответь командой /meta на PNG/WebP/JPEG файл или картинку с metadata NovelAI.")
        return
    tg_file = await message.bot.get_file(file_id)
    bio = BytesIO()
    await message.bot.download_file(tg_file.file_path, destination=bio)
    meta = parse_nai_metadata(bio.getvalue())
    set_last_metadata(message.from_user.id, meta)
    await message.answer(metadata_summary(meta), parse_mode="HTML", reply_markup=meta_import_menu() if meta else main_menu())

@dp.message(Command("reset_settings"))
async def reset_settings_cmd(message: types.Message):
    await message.answer(
        "♻️ Сбросить настройки генерации к безопасным значениям?",
        reply_markup=confirm_reset_menu("settings"),
    )

@dp.message(Command("ar_reset"))
async def ar_reset_cmd(message: types.Message):
    s = get_settings(message.from_user.id)
    if message.from_user.id not in ADMIN_IDS or not s.artraccoon_mode:
        await message.answer("Команда не найдена.")
        return
    await message.answer(
        "🦝 Сбросить сохранённые ArtRaccoon-промты? Режим останется включённым.",
        reply_markup=confirm_reset_menu("ar"),
    )

@dp.message(Command("generation_settings"))
async def generation_settings_cmd(message: types.Message):
    s = get_settings(message.from_user.id)
    await message.answer(settings_text(message.from_user.id), reply_markup=settings_markup_for(message.from_user.id), parse_mode="HTML")

@dp.message(Command("settings"))
async def settings_cmd(message: types.Message):
    s = get_settings(message.from_user.id)
    await message.answer(settings_text(message.from_user.id), reply_markup=settings_markup_for(message.from_user.id), parse_mode="HTML")

@dp.message(Command("pro"))
async def pro_cmd(message: types.Message):
    if message.from_user.id not in ADMIN_IDS:
        patch_settings(message.from_user.id, pro_mode=False)
        await message.answer("💎 Эта функция временно отключена.")
        return
    s = get_settings(message.from_user.id)
    patch_settings(message.from_user.id, pro_mode=not s.pro_mode)
    s = get_settings(message.from_user.id)
    await message.answer("💎 PRO режим включён. Расширенные функции могут тратить Anlas." if s.pro_mode else "✅ Обычный режим включён. Дорогие функции скрыты.", reply_markup=settings_markup_for(message.from_user.id))

@dp.message(Command("ArtRaccoonoff"))
async def artraccoon_off_cmd(message: types.Message):
    if message.from_user.id not in ADMIN_IDS:
        await message.answer("Команда не найдена.")
        return
    patch_settings(message.from_user.id, artraccoon_mode=False)
    await message.answer("🦝 ArtRaccoon режим выключен. Сохранённые настройки не удалены.", reply_markup=main_menu())

@dp.message(Command("ArtRaccoonon"))
async def artraccoon_on_cmd(message: types.Message):
    if message.from_user.id not in ADMIN_IDS:
        await message.answer("Команда не найдена.")
        return
    patch_settings(message.from_user.id, artraccoon_mode=True, pro_mode=True)
    await message.answer("🦝 ArtRaccoon режим включён. Текст теперь считается Character Prompt.", reply_markup=artraccoon_menu())

@dp.message(Command("ar"))
async def ar_cmd(message: types.Message):
    s = get_settings(message.from_user.id)
    if message.from_user.id not in ADMIN_IDS or not s.artraccoon_mode:
        await message.answer("Команда не найдена.")
        return
    await message.answer("🦝 <b>ArtRaccoon панель</b>", parse_mode="HTML", reply_markup=artraccoon_menu())

@dp.message(Command("ar_settings"))
async def ar_settings_cmd(message: types.Message):
    s = get_settings(message.from_user.id)
    if message.from_user.id not in ADMIN_IDS or not s.artraccoon_mode:
        await message.answer("Команда не найдена.")
        return
    def brief(label, value):
        return f"<b>{label}</b> ({len(value)} симв.):\n<code>{html.escape(value[:500] or '—')}</code>"
    await message.answer("🦝 <b>ArtRaccoon настройки</b>\n\n" + "\n\n".join([brief("Base Prompt", s.artraccoon_base_prompt), brief("Base UC", s.artraccoon_base_uc), brief("Character Prompt", s.artraccoon_character_prompt), brief("Character UC", s.artraccoon_character_uc or s.artraccoon_character_negative), brief("Character Position", s.artraccoon_character_position)]), parse_mode="HTML", reply_markup=artraccoon_menu())

@dp.message(Command("ar_show"))
async def ar_show_cmd(message: types.Message):
    s = get_settings(message.from_user.id)
    if message.from_user.id not in ADMIN_IDS or not s.artraccoon_mode:
        await message.answer("Команда не найдена.")
        return
    for label, value in [("Base Prompt", s.artraccoon_base_prompt), ("Base UC", s.artraccoon_base_uc), ("Character Prompt", s.artraccoon_character_prompt), ("Character UC", s.artraccoon_character_uc or s.artraccoon_character_negative), ("Character Position", s.artraccoon_character_position)]:
        await message.answer(f"<b>{label}</b>\n<code>{html.escape(value or '—')}</code>", parse_mode="HTML")

@dp.message(Command("ar_payload"))
async def ar_payload_cmd(message: types.Message):
    s = get_settings(message.from_user.id)
    if message.from_user.id not in ADMIN_IDS or not s.artraccoon_mode:
        await message.answer("Команда не найдена.")
        return
    s = patch_settings(message.from_user.id, artraccoon_force_concat=not s.artraccoon_force_concat)
    await message.answer(
        "🧪 <b>ArtRaccoon payload mode</b>\n"
        f"Character Payload: <code>{'OFF' if s.artraccoon_force_concat else 'ON'}</code>\n"
        f"Fallback concat: <code>{'ON' if s.artraccoon_force_concat else 'OFF'}</code>",
        parse_mode="HTML",
    )

@dp.message(Command("ar_show_payload"))
async def ar_show_payload_cmd(message: types.Message):
    s = get_settings(message.from_user.id)
    if message.from_user.id not in ADMIN_IDS or not s.artraccoon_mode:
        await message.answer("Команда не найдена.")
        return
    preview = nai.safe_prompt_preview(s.artraccoon_base_prompt, s)
    negatives = (
        f"base length={preview['negative_base_length']}; "
        f"character length={preview['negative_character_length']}; "
        f"character payload={'yes' if preview['negative_character_payload'] else 'no'}"
    )
    lines = [
        "🧪 <b>ArtRaccoon payload preview</b>",
        f"Model: <code>{html.escape(str(preview['model']))}</code>",
        f"Base Prompt length: <code>{preview['base_prompt_length']}</code>",
        f"Character Prompt length: <code>{preview['character_prompt_length']}</code>",
        f"Current mode: <code>{html.escape(ar_payload_mode(s))}</code>",
        f"Fallback forced: <code>{'yes' if s.artraccoon_force_concat else 'no'}</code>",
        f"Has character payload: <code>{'yes' if preview['has_character_payload'] else 'no'}</code>",
        f"Negative parts summary: <code>{html.escape(negatives)}</code>",
    ]
    await message.answer("\n".join(lines), parse_mode="HTML")

@dp.message(Command("raw"))
async def raw_cmd(message: types.Message):
    s = get_settings(message.from_user.id)
    await message.answer(f"<pre>{s.to_dict()}</pre>", parse_mode="HTML")


@dp.message(Command("nai_debug"))
async def nai_debug_cmd(message: types.Message):
    s = get_settings(message.from_user.id)
    debug = nai.debug_settings(s)
    lines = [f"{key}: {value}" for key, value in debug.items()]
    await message.answer(
        "🧪 <b>NovelAI debug</b>\n"
        f"<pre>{html.escape(chr(10).join(lines))}</pre>",
        parse_mode="HTML",
    )

@dp.message(Command("nai_payload"))
async def nai_payload_cmd(message: types.Message):
    if message.from_user.id not in ADMIN_IDS:
        await message.answer("Команда не найдена.")
        return
    s = get_settings(message.from_user.id)
    payload = get_last_payload(message.from_user.id) or sanitize_payload(nai.build_payload(s.last_prompt or "debug prompt", s))
    await message.answer(nai_payload_summary_text(payload, s), parse_mode="HTML")



@dp.message(Command("nai_compare"))
async def nai_compare_cmd(message: types.Message):
    if message.from_user.id not in ADMIN_IDS:
        await message.answer("Команда не найдена.")
        return
    meta = get_last_metadata(message.from_user.id)
    payload = sanitize_payload(get_last_payload(message.from_user.id))
    if not meta:
        await message.answer("📭 Last NovelAI metadata не сохранена. Ответь /meta на файл с metadata NovelAI.")
        return
    if not payload:
        await message.answer("📭 Last bot payload не сохранён. Сгенерируй изображение или используй /nai_payload для preview.")
        return
    await message.answer(nai_compare_summary_text(meta, payload), parse_mode="HTML")


@dp.message(Command("nai_payload_full"))
async def nai_payload_full_cmd(message: types.Message):
    if message.from_user.id not in ADMIN_IDS:
        await message.answer("Команда не найдена.")
        return
    payload = sanitize_payload(get_last_payload(message.from_user.id))
    if not payload:
        await message.answer("📭 Last NovelAI payload пока не сохранён. Сгенерируй изображение или используй /nai_payload для preview.")
        return
    data = json.dumps(payload, ensure_ascii=False, indent=2).encode("utf-8")
    await message.answer_document(BufferedInputFile(data, filename="novelai_payload.json"))


@dp.message(Command("nai_site_mode"))
async def nai_site_mode_cmd(message: types.Message):
    if message.from_user.id not in ADMIN_IDS:
        await message.answer("Команда не найдена.")
        return
    s = get_settings(message.from_user.id)
    enabling = not s.nai_site_mode
    updates = {"nai_site_mode": enabling}
    if enabling:
        updates.update({
            "steps": SITE_MODE_STEPS,
            "scale": SITE_MODE_SCALE,
            "cfg_rescale": SITE_MODE_CFG_RESCALE,
            "sampler": SITE_MODE_SAMPLER,
            "noise_schedule": SITE_MODE_NOISE_SCHEDULE,
            "n_samples": 1,
            "add_quality_tags": True,
            "variety_plus": True,
        })
    s = patch_settings(message.from_user.id, **updates)
    await message.answer(
        "🌐 Website-compatible mode: "
        f"<code>{'ON' if s.nai_site_mode else 'OFF'}</code>",
        parse_mode="HTML",
    )


async def notify_admins_about_prompt(message: types.Message, prompt: str) -> None:
    """Отправляет админу промт и настройки пользователя для мягкой модерации."""
    if not ADMIN_IDS:
        return

    user = message.from_user
    if user is None:
        return

    # Пользователь просил получать вводы других пользователей.
    if user.id in ADMIN_IDS:
        return

    s = get_settings(user.id)
    username = f"@{user.username}" if user.username else "без username"
    full_name = user.full_name or "без имени"

    text = (
        "🧾 <b>Новый запрос генерации</b>\\n\\n"
        f"👤 Пользователь: <code>{full_name}</code>\\n"
        f"🔗 Username: <code>{username}</code>\\n"
        f"🆔 ID: <code>{user.id}</code>\\n\\n"
        f"📝 <b>Prompt:</b>\\n<code>{prompt[:3000]}</code>\\n\\n"
        "⚙️ <b>Настройки:</b>\\n"
        f"Модель: <code>{s.model_name}</code>\\n"
        f"Размер: <code>{s.width}x{s.height}</code>\\n"
        f"Картинок: <code>{s.n_samples}</code>\\n"
        f"Steps: <code>{s.steps}</code>\\n"
        f"Guidance: <code>{s.scale}</code>\\n"
        f"Sampler: <code>{s.sampler}</code>\\n"
        f"Seed: <code>{s.seed}</code>\\n"
        f"UC preset: <code>{s.uc_preset}</code>\\n"
        f"Negative: <code>{(s.negative_prompt or '—')[:800]}</code>"
    )

    for admin_id in ADMIN_IDS:
        try:
            await message.bot.send_message(admin_id, text, parse_mode="HTML")
        except Exception:
            log.exception("Failed to notify admin %s", admin_id)


async def generate_image_from_prompt(
    message: types.Message,
    prompt: str,
    actor: types.User | None = None,
) -> None:
    user = actor or message.from_user
    if user is None:
        await message.answer("Не вижу пользователя. Попробуй ещё раз.")
        return

    user_id = user.id
    if user_id not in ADMIN_IDS and remaining_generations(user_id) == 0:
        await message.answer("🎨 Лимит на сегодня закончился: 10/10 генераций. Попробуй завтра.", reply_markup=main_menu())
        return

    cd = cooldown_remaining(user_id)
    if cd > 0:
        await message.answer(f"⏳ Подожди ещё {cd} сек. перед следующей генерацией.", reply_markup=main_menu())
        return

    s = patch_settings(user_id, last_prompt=prompt)
    s = apply_anlas_safe_defaults(user_id)
    original_prompt = s.pending_original_prompt or prompt
    try:
        final_prompt = nai.build_prompt(prompt, s)
    except Exception:
        final_prompt = prompt

    global generation_waiting
    if generation_lock.locked() or generation_waiting:
        await message.answer(f"⏳ Генерация поставлена в очередь. Перед тобой: {generation_waiting}")
    generation_waiting += 1
    wait = await message.answer("🎨 Генерирую...")

    image_bytes = None
    if s.pending_image_path and Path(s.pending_image_path).exists():
        image_bytes = Path(s.pending_image_path).read_bytes()
    elif message.reply_to_message and message.reply_to_message.photo:
        photo = message.reply_to_message.photo[-1]
        file = await message.bot.get_file(photo.file_id)
        bio = BytesIO()
        await message.bot.download_file(file.file_path, destination=bio)
        image_bytes = bio.getvalue()

    async def show_character_payload_fallback() -> None:
        await wait.edit_text("NovelAI не принял Character Payload, пробую fallback-сборку.")

    try:
        async with generation_lock:
            generation_waiting = max(0, generation_waiting - 1)
            mark_generation_started(user_id)
            candidates = learn_from_english_prompt(prompt) if looks_like_english_tags(prompt) else []
            await send_moderation_copy(message.bot, user, original_prompt, final_prompt, s, candidates)
            images = await asyncio.wait_for(
                nai.generate(
                    prompt,
                    s,
                    image_bytes=image_bytes,
                    on_character_payload_fallback=show_character_payload_fallback,
                ),
                timeout=GENERATION_TIMEOUT_SECONDS,
            )
            set_last_payload(user_id, sanitize_payload(nai.last_payload))
        timestamp = datetime.now(timezone.utc).isoformat()
        saved_images = _save_generated_images(user_id, timestamp, images)
        history_item = {
            "prompt": prompt,
            "original_prompt": original_prompt,
            "final_prompt": final_prompt,
            "negative_prompt": s.negative_prompt,
            "seed": s.seed,
            "model": s.model_name,
            "size": f"{s.width}x{s.height}",
            "timestamp": timestamp,
            "image": {"count": len(images), "format": "png"} if images else {},
            "images": saved_images,
        }
        add_history(user_id, history_item)
        set_last_metadata(user_id, history_item)
        patch_settings(user_id, pending_image_path="")
        await wait.delete()

        for idx, img in enumerate(images, start=1):
            name = f"novelai_{idx}.png"
            image = BufferedInputFile(img, filename=name)
            caption = f"✅ <b>Готово</b>\\nSeed: <code>{s.seed}</code>\\nРазмер: <code>{s.width}x{s.height}</code>"
            try:
                await send_moderation_image(message.bot, user, img, original_prompt, final_prompt, s, idx, candidates)
                await message.answer_photo(
                    image,
                    caption=caption,
                    parse_mode="HTML",
                    reply_markup=after_generation_menu(),
                )
            except Exception:
                log.exception("Failed to send image as photo, sending as document")
                await message.answer_document(
                    BufferedInputFile(img, filename=name),
                    caption=caption,
                    parse_mode="HTML",
                    reply_markup=after_generation_menu(),
                )

    except asyncio.TimeoutError:
        await wait.edit_text(
            "⏱ NovelAI слишком долго отвечает. Попробуй ещё раз позже.",
            reply_markup=main_menu(),
        )
    except NovelAIError as e:
        await wait.edit_text(
            f"❌ NovelAI не смог сгенерировать изображение. {str(e)[:3300]}",
            reply_markup=main_menu(),
        )
    except Exception:
        log.exception("Generation failed")
        await wait.edit_text(
            "❌ Внутренняя ошибка бота. Попробуй позже или измени промт.",
            reply_markup=main_menu(),
        )


@dp.message(Command("gen"))
async def gen_cmd(message: types.Message):
    prompt = message.text.replace("/gen", "", 1).strip() if message.text else ""
    if not prompt:
        await message.answer(
            "Напиши так:\n<code>/gen raccoon girl, pink eyes, sketch</code>",
            parse_mode="HTML",
            reply_markup=main_menu(),
        )
        return

    s = get_settings(message.from_user.id)
    if s.artraccoon_mode:
        patch_settings(message.from_user.id, artraccoon_character_prompt=prompt)
        prompt = s.artraccoon_base_prompt
    await generate_image_from_prompt(message, prompt)

@dp.message(Command("presets"))
async def presets_cmd(message: types.Message):
    await message.answer(presets_text(), reply_markup=presets_menu(), parse_mode="HTML")


@dp.message(Command("last_prompt"))
async def last_prompt_cmd(message: types.Message):
    await send_last_prompt(message)


@dp.message(Command("retry"))
async def retry_cmd(message: types.Message):
    await retry_last_prompt(message)


@dp.message(Command("seed"))
async def seed_cmd(message: types.Message):
    value = message.text.replace("/seed", "", 1).strip() if message.text else ""
    if not value:
        await message.answer("🎲 Напиши <code>/seed random</code> или <code>/seed 123456</code>.", parse_mode="HTML", reply_markup=main_menu())
        return
    if value.lower() == "random":
        patch_settings(message.from_user.id, seed=-1)
        await message.answer("🎲 Seed переключён в random. Каждый раз будет новый результат.", reply_markup=main_menu())
        return
    try:
        seed = int(value)
    except ValueError:
        await message.answer("Не поняла seed 😅 Используй число, например <code>/seed 123456</code>, или <code>/seed random</code>.", parse_mode="HTML", reply_markup=main_menu())
        return
    if seed < 0 or seed > 4294967295:
        await message.answer("Seed должен быть от 0 до 4294967295. Для случайного seed напиши <code>/seed random</code>.", parse_mode="HTML", reply_markup=main_menu())
        return
    patch_settings(message.from_user.id, seed=seed)
    await message.answer(f"🎯 Seed сохранён: <code>{seed}</code>. Теперь /retry повторит промт с этим seed.", parse_mode="HTML", reply_markup=main_menu())


@dp.message(Command("draw"))
async def draw_cmd(message: types.Message):
    prompt = message.text.replace("/draw", "", 1).strip() if message.text else ""
    if not prompt:
        await message.answer(
            "Напиши так:\n<code>/draw raccoon girl, pink eyes, sketch</code>",
            parse_mode="HTML",
            reply_markup=main_menu(),
        )
        return

    s = get_settings(message.from_user.id)
    if s.artraccoon_mode:
        patch_settings(message.from_user.id, artraccoon_character_prompt=prompt)
        prompt = s.artraccoon_base_prompt
    await generate_image_from_prompt(message, prompt)

@dp.callback_query(F.data == "menu:main")
async def cb_main(call: types.CallbackQuery):
    await call.message.edit_text(
        "🦝 <b>Главное меню</b>\n\n"
        "Выбери действие: новый промт, быстрый пресет, повтор последней генерации или настройки.",
        reply_markup=main_menu(),
        parse_mode="HTML",
    )
    await call.answer()

@dp.callback_query(F.data == "menu:settings")
async def cb_settings(call: types.CallbackQuery):
    await call.message.edit_text(settings_text(call.from_user.id), reply_markup=settings_markup_for(call.from_user.id), parse_mode="HTML")
    await call.answer()

@dp.callback_query(F.data == "reset:ask")
async def cb_reset_ask(call: types.CallbackQuery):
    await call.message.edit_text(
        "♻️ Сбросить настройки генерации к безопасным значениям?",
        reply_markup=confirm_reset_menu("settings"),
    )
    await call.answer()

@dp.callback_query(F.data == "reset:cancel")
async def cb_reset_cancel(call: types.CallbackQuery):
    await call.message.edit_text(settings_text(call.from_user.id), reply_markup=settings_markup_for(call.from_user.id), parse_mode="HTML")
    await call.answer("Отменено")

@dp.callback_query(F.data.startswith("reset:confirm:"))
async def cb_reset_confirm(call: types.CallbackQuery):
    kind = call.data.rsplit(":", 1)[-1]
    if kind == "ar":
        s = get_settings(call.from_user.id)
        if call.from_user.id not in ADMIN_IDS or not s.artraccoon_mode:
            await call.answer("Команда не найдена.", show_alert=True)
            return
        patch_settings(call.from_user.id, **artraccoon_prompt_defaults())
        await call.message.edit_text("🦝 ArtRaccoon-промты очищены. Режим остался включённым.", reply_markup=artraccoon_menu())
        await call.answer("Сброшено")
        return
    patch_settings(call.from_user.id, **safe_generation_defaults())
    await call.message.edit_text("♻️ Настройки генерации сброшены к безопасным значениям.", reply_markup=settings_markup_for(call.from_user.id))
    await call.answer("Сброшено")

@dp.callback_query(F.data == "menu:gen")
async def cb_gen(call: types.CallbackQuery, state: FSMContext):
    await state.set_state(GenState.waiting_prompt)
    await call.message.edit_text(
        "🎨 <b>Генерация</b>\n\n"
        "Отправь промт обычным сообщением.\n\n"
        "Пример:\n"
        "<code>1girl, raccoon ears, pink eyes, ruins, sketch</code>\n\n"
        "Чтобы отменить: /cancel",
        reply_markup=main_menu(),
        parse_mode="HTML",
    )
    await call.answer()

@dp.callback_query(F.data.in_({"menu:help", "menu:howto"}))
async def cb_help(call: types.CallbackQuery):
    await call.message.edit_text(
        howto_text(call.from_user.id),
        reply_markup=main_menu(),
        parse_mode="HTML",
    )
    await call.answer()

@dp.callback_query(F.data == "menu:img2img")
async def cb_img2img(call: types.CallbackQuery):
    await call.message.edit_text(
        "📎 <b>Img2Img</b>\n\n"
        "1. Отправь картинку боту\n"
        "2. Ответь на неё командой:\n"
        "<code>/gen что нужно получить</code>\n\n"
        "Strength/noise можно менять в ⚙️ Настройки → 📎 Img2Img сила.",
        reply_markup=main_menu(),
        parse_mode="HTML",
    )
    await call.answer()

@dp.callback_query(F.data == "menu:presets")
async def cb_presets(call: types.CallbackQuery):
    await call.message.edit_text(presets_text(), reply_markup=presets_menu(), parse_mode="HTML")
    await call.answer()

@dp.callback_query(F.data.startswith("preset:show:"))
async def cb_preset_show(call: types.CallbackQuery):
    key = call.data.split(":", 2)[2]
    preset = QUICK_PRESETS.get(key)
    if not preset:
        await call.answer("Пресет не найден", show_alert=True)
        return
    prompt = preset["prompt"]
    current = patch_settings(call.from_user.id, pending_prompt=prompt, pending_original_prompt="", prompt_action="")
    await call.message.edit_text(
        f"✍️ <b>{preset['title']}</b> — сохранила как черновик.\n\n"
        + prompt_preview_text(prompt),
        reply_markup=prompt_menu_for(current, call.from_user.id),
        parse_mode="HTML",
    )
    await call.answer("Промт готов")

@dp.callback_query(F.data.startswith("preset:gen:"))
async def cb_preset_gen(call: types.CallbackQuery):
    key = call.data.split(":", 2)[2]
    preset = QUICK_PRESETS.get(key)
    if not preset:
        await call.answer("Пресет не найден", show_alert=True)
        return
    await call.answer("Запускаю генерацию")
    await call.message.answer(f"⚡ Генерирую пресет: <b>{preset['title']}</b>", parse_mode="HTML")
    await generate_image_from_prompt(call.message, preset["prompt"], actor=call.from_user)

@dp.callback_query(F.data == "quick:last_prompt")
async def cb_last_prompt(call: types.CallbackQuery):
    await send_last_prompt(call.message, actor=call.from_user)
    await call.answer()

@dp.callback_query(F.data == "quick:retry")
async def cb_retry(call: types.CallbackQuery):
    await call.answer("Повторяю")
    await retry_last_prompt(call.message, actor=call.from_user)



@dp.callback_query(F.data == "prompt:show_original")
async def cb_show_original(call: types.CallbackQuery):
    s = get_settings(call.from_user.id)
    if not s.pending_prompt.strip():
        await call.answer("Черновик пуст", show_alert=True)
        return
    await call.message.edit_text(
        prompt_preview_text(s.pending_prompt, s.pending_original_prompt, s),
        parse_mode="HTML",
        reply_markup=prompt_menu_for(s, call.from_user.id),
    )
    await call.answer("Показываю исходник")

@dp.callback_query(F.data == "prompt:confirm")
async def cb_prompt_confirm(call: types.CallbackQuery):
    s = get_settings(call.from_user.id)
    prompt = s.pending_prompt.strip()
    if s.artraccoon_mode:
        prompt = s.artraccoon_base_prompt.strip()
    if not prompt and not (s.artraccoon_mode and s.artraccoon_character_prompt.strip()):
        await call.answer("Черновик пуст", show_alert=True)
        await call.message.answer("📝 Черновик пуст. Пришли новый промт обычным сообщением.", reply_markup=main_menu())
        return
    patch_settings(call.from_user.id, pending_prompt="", pending_original_prompt="", prompt_action="")
    await call.answer("Запускаю генерацию")
    await call.message.answer("✅ Отлично, запускаю генерацию по черновику.")
    await generate_image_from_prompt(call.message, prompt, actor=call.from_user)

@dp.callback_query(F.data == "prompt:append")
async def cb_prompt_append(call: types.CallbackQuery):
    if not get_settings(call.from_user.id).pending_prompt.strip():
        await call.answer("Сначала пришли промт", show_alert=True)
        return
    patch_settings(call.from_user.id, prompt_action="append")
    await call.message.answer("✏️ Пришли текст, который нужно дописать к текущему промту. Я добавлю его в черновик.")
    await call.answer()

@dp.callback_query(F.data == "prompt:replace")
async def cb_prompt_replace(call: types.CallbackQuery):
    patch_settings(call.from_user.id, prompt_action="replace")
    await call.message.answer("🔁 Хорошо, пришли новый промт — я заменю текущий черновик.")
    await call.answer()

@dp.callback_query(F.data == "prompt:cancel")
async def cb_prompt_cancel(call: types.CallbackQuery):
    patch_settings(call.from_user.id, pending_prompt="", pending_original_prompt="", prompt_action="")
    await call.message.answer("❌ Черновик очищен. Когда будешь готов — пришли новый промт обычным сообщением.", reply_markup=main_menu())
    await call.answer("Отменено")

@dp.callback_query(F.data.startswith("settings:"))
async def cb_setting_text_input(call: types.CallbackQuery, state: FSMContext):
    field = call.data.split(":", 1)[1]
    s = get_settings(call.from_user.id)
    advanced = (s.pro_mode and call.from_user.id in ADMIN_IDS) or s.artraccoon_mode
    if call.from_user.id not in ADMIN_IDS and field in {"n", "sampler", "uc", "cfg", "noise", "img2img", "modes"}:
        patch_settings(call.from_user.id, pro_mode=False, n_samples=1)
        await call.answer("💎 Эта функция временно отключена.", show_alert=True)
        await call.message.answer("💎 Эта функция временно отключена.", reply_markup=main_menu())
        return
    if field == "model":
        await call.message.edit_text("🧠 Выбери модель:", reply_markup=model_menu())
        await call.answer()
        return
    if field == "size":
        await call.message.edit_text("📐 Выбери размер:", reply_markup=size_menu())
        await call.answer()
        return
    if field == "sampler":
        await call.message.edit_text("🎛 Выбери sampler:", reply_markup=sampler_menu())
        await call.answer()
        return
    if field == "uc":
        await call.message.edit_text("🧪 Выбери UC-пресет:", reply_markup=uc_menu())
        await call.answer()
        return
    if field == "noise":
        await call.message.edit_text("🌊 Выбери noise schedule:", reply_markup=noise_menu())
        await call.answer()
        return
    if field == "seed":
        await call.message.edit_text("🎲 Seed:", reply_markup=seed_menu())
        await call.answer()
        return
    if field == "n":
        if not advanced:
            await call.answer("💎 Эта функция временно отключена.", show_alert=True)
            return
        await call.message.edit_text("🖼 Количество картинок:", reply_markup=samples_menu())
        await call.answer()
        return
    if field == "modes":
        await call.message.edit_text("🦝 Режимы:", reply_markup=modes_menu(s.furry_mode, s.background_mode, s.add_quality_tags, s.variety_plus))
        await call.answer()
        return
    prompt = SETTING_PROMPTS.get(field)
    if not prompt:
        await call.answer("Неизвестная настройка", show_alert=True)
        return
    await state.set_state(GenState.waiting_setting)
    await state.update_data(setting_field=field)
    await call.message.answer(prompt + "\n\n/cancel — отменить ввод.", parse_mode="HTML")
    await call.answer()

@dp.callback_query(F.data.startswith("settings_input:"))
async def cb_settings_input(call: types.CallbackQuery, state: FSMContext):
    field = call.data.split(":", 1)[1]
    prompt = SETTING_PROMPTS.get(field)
    if not prompt:
        await call.answer("Неизвестная настройка", show_alert=True)
        return
    await state.set_state(GenState.waiting_setting)
    await state.update_data(setting_field=field)
    await call.message.answer(prompt + "\n\n/cancel — отменить ввод.", parse_mode="HTML")
    await call.answer()

@dp.callback_query(F.data == "settings:modes")
async def cb_modes(call: types.CallbackQuery):
    s = get_settings(call.from_user.id)
    await call.message.edit_text(
        "🦝 Режимы:",
        reply_markup=modes_menu(s.furry_mode, s.background_mode, s.add_quality_tags, s.variety_plus)
    )
    await call.answer()




def _format_timestamp(value: str) -> str:
    if not value:
        return "—"
    try:
        return datetime.fromisoformat(value).strftime("%Y-%m-%d %H:%M UTC")
    except ValueError:
        return value[:19]

def _item_prompt(item: dict) -> str:
    return str(item.get("final_prompt") or item.get("prompt") or "")

def _history_lines(items: list[dict], title: str, empty_text: str) -> str:
    if not items:
        return f"{title}\n\n{empty_text}"
    lines = [title, ""]
    for i, item in enumerate(items[:10], start=1):
        prompt = html.escape(_item_prompt(item)[:160] or "—")
        lines.append(
            f"{i}. <code>{prompt}</code>\n"
            f"   🎲 <code>{item.get('seed', '—')}</code> · "
            f"🧠 <code>{html.escape(str(item.get('model', '—')))}</code> · "
            f"📐 <code>{item.get('size', '—')}</code> · "
            f"🕒 <code>{html.escape(_format_timestamp(str(item.get('timestamp', ''))))}</code>"
        )
    return "\n".join(lines)

def _last_generation_item(user_id: int) -> dict | None:
    meta = get_last_metadata(user_id)
    if meta.get("prompt") or meta.get("final_prompt"):
        return dict(meta)
    s = get_settings(user_id)
    if not s.last_prompt:
        return None
    return {
        "prompt": s.last_prompt,
        "original_prompt": s.last_prompt,
        "final_prompt": s.last_prompt,
        "negative_prompt": s.negative_prompt,
        "seed": s.seed,
        "model": s.model_name,
        "size": f"{s.width}x{s.height}",
        "timestamp": datetime.now(timezone.utc).isoformat(),
    }

def _item_prompt_text(item: dict) -> str:
    return (
        "📝 <b>Промт</b>\n"
        f"<blockquote expandable>{html.escape(str(item.get('prompt') or '—'))}</blockquote>\n"
        "🎯 <b>Final prompt</b>\n"
        f"<blockquote expandable>{html.escape(str(item.get('final_prompt') or item.get('prompt') or '—'))}</blockquote>\n"
        "🚫 <b>Negative prompt</b>\n"
        f"<blockquote expandable>{html.escape(str(item.get('negative_prompt') or '—'))}</blockquote>"
    )

def _item_caption(item: dict, number: int) -> str:
    return (
        f"#{number}\n"
        f"🕒 <code>{html.escape(_format_timestamp(str(item.get('timestamp', ''))))}</code>\n"
        f"🎲 Seed: <code>{html.escape(str(item.get('seed', '—')))}</code>\n"
        f"📐 Size: <code>{html.escape(str(item.get('size', '—')))}</code>\n"
        f"🧠 Model: <code>{html.escape(str(item.get('model', '—')))}</code>"
    )

def _first_item_image_path(item: dict) -> Path | None:
    images = item.get("images")
    if not isinstance(images, list):
        return None
    for image in images:
        if isinstance(image, dict):
            path = _safe_existing_generated_path(str(image.get("path") or ""))
            if path:
                return path
    return None

async def _send_collection_preview(message: types.Message, kind: str, items: list[dict]) -> None:
    if not items:
        return
    item = items[0]
    image_path = _first_item_image_path(item)
    if image_path:
        await message.answer_photo(
            FSInputFile(image_path),
            caption=_item_caption(item, 1),
            parse_mode="HTML",
            reply_markup=generation_item_menu(kind, 0),
        )
    else:
        await message.answer(
            "Изображение не найдено на диске, показываю только данные.\n\n" + _item_caption(item, 1),
            parse_mode="HTML",
            reply_markup=generation_item_menu(kind, 0),
        )

def _history_keyboard(kind: str, items: list[dict]):
    from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton
    buttons = []
    for i, item in enumerate(items[:10], start=1):
        buttons.append(InlineKeyboardButton(text=f"{i}. 🔁", callback_data=f"{kind}:retry:{i-1}"))
        if kind == "history":
            buttons.append(InlineKeyboardButton(text=f"{i}. ⭐", callback_data=f"history:fav:{i-1}"))
        else:
            buttons.append(InlineKeyboardButton(text=f"{i}. 🗑", callback_data=f"fav:del:{i-1}"))
        buttons.append(InlineKeyboardButton(text=f"{i}. 📝", callback_data=f"{kind}:prompt:{i-1}"))
    buttons.append(InlineKeyboardButton(text="⬅️ Меню", callback_data="menu:main"))
    return InlineKeyboardMarkup(inline_keyboard=[buttons[i:i+3] for i in range(0, len(buttons), 3)])


def transform_prompt(prompt: str, tool: str) -> str:
    p = " ".join(prompt.replace("\n", ", ").split())
    if tool == "clean":
        parts = []
        for part in [x.strip() for x in p.split(",")]:
            if part and part.lower() not in [x.lower() for x in parts]:
                parts.append(part)
        return ", ".join(parts)
    if tool == "translate":
        return natural_to_nai_tags(prompt)
    additions = {
        "improve": "strong composition, expressive lighting, detailed background, cohesive color palette, sharp focus",
        "raccoon": "ArtRaccoon vibe, cozy mischievous raccoon energy, warm cinematic light, whimsical details",
        "aelita": AELITA_DESCRIPTION,
    }
    addition = additions.get(tool, "")
    return f"{p}, {addition}".strip(", ")

@dp.message(Command("history"))
async def history_cmd(message: types.Message):
    items = get_history(message.from_user.id)
    await message.answer(
        _history_lines(items, "🕘 <b>История генераций</b>", "История пока пустая. Сгенерируй картинку — и я сохраню её здесь 🦝"),
        parse_mode="HTML",
        reply_markup=_history_keyboard("history", items) if items else main_menu(),
    )
    await _send_collection_preview(message, "history", items)

@dp.message(Command("favorites"))
async def favorites_cmd(message: types.Message):
    items = get_favorites(message.from_user.id)
    await message.answer(
        _history_lines(items, "⭐ <b>Избранное</b>", "В избранном пока пусто. Нажми ⭐ после удачной генерации — и она появится здесь."),
        parse_mode="HTML",
        reply_markup=_history_keyboard("fav", items) if items else main_menu(),
    )
    await _send_collection_preview(message, "fav", items)

@dp.callback_query(F.data == "menu:history")
async def cb_history(call: types.CallbackQuery):
    items = get_history(call.from_user.id)
    await call.message.edit_text(_history_lines(items, "🕘 <b>История генераций</b>", "История пока пустая. Сгенерируй картинку — и я сохраню её здесь 🦝"), parse_mode="HTML", reply_markup=_history_keyboard("history", items) if items else main_menu())
    await _send_collection_preview(call.message, "history", items)
    await call.answer()

@dp.callback_query(F.data == "menu:favorites")
async def cb_favorites(call: types.CallbackQuery):
    items = get_favorites(call.from_user.id)
    await call.message.edit_text(_history_lines(items, "⭐ <b>Избранное</b>", "В избранном пока пусто. Нажми ⭐ после удачной генерации — и она появится здесь."), parse_mode="HTML", reply_markup=_history_keyboard("fav", items) if items else main_menu())
    await _send_collection_preview(call.message, "fav", items)
    await call.answer()

@dp.callback_query(F.data == "favorite:last")
async def cb_favorite_last(call: types.CallbackQuery):
    item = _last_generation_item(call.from_user.id)
    if not item:
        await call.answer("Пока нечего добавить", show_alert=True)
        return
    add_favorite(call.from_user.id, item)
    await call.answer("Добавлено в избранное ⭐")

@dp.callback_query(F.data.regexp(r"^(history|fav):(retry|prompt|fav|del):\d+$"))
async def cb_history_favorite_action(call: types.CallbackQuery):
    kind, action, raw_index = call.data.split(":")
    index = int(raw_index)
    items = get_history(call.from_user.id) if kind == "history" else get_favorites(call.from_user.id)
    if index >= len(items):
        await call.answer("Запись не найдена", show_alert=True)
        return
    item = items[index]
    if action == "retry":
        await call.answer("Повторяю")
        await generate_image_from_prompt(call.message, str(item.get("prompt") or item.get("final_prompt") or ""), actor=call.from_user)
    elif action == "prompt":
        await call.message.answer(_item_prompt_text(item), parse_mode="HTML")
        await call.answer("Показываю промт")
    elif action == "fav" and kind == "history":
        add_favorite(call.from_user.id, item)
        await call.answer("Добавлено в избранное ⭐")
    elif action == "del" and kind == "fav":
        delete_favorite(call.from_user.id, index)
        await call.answer("Удалено")
        items = get_favorites(call.from_user.id)
        await call.message.edit_text(_history_lines(items, "⭐ <b>Избранное</b>", "В избранном пока пусто. Нажми ⭐ после удачной генерации — и она появится здесь."), parse_mode="HTML", reply_markup=_history_keyboard("fav", items) if items else main_menu())

@dp.callback_query(F.data.in_({"menu:inpaint", "menu:reference", "menu:upscale"}))
async def cb_placeholders(call: types.CallbackQuery):
    if not get_settings(call.from_user.id).pro_mode:
        await call.message.edit_text("💎 Это PRO/Anlas-связанная функция. Сейчас она заблокирована в экономном режиме: может тратить Anlas. Включи 💎 PRO / Анласы, чтобы разрешить дорогие режимы.", reply_markup=main_menu())
    else:
        texts = {
            "menu:inpaint": "🩹 Инпейнт — PRO/Anlas-связанная функция. Поддержка масок будет добавлена позже.",
            "menu:reference": "🧬 Референс / вайб — PRO/Anlas-связанная функция. Workflow будет добавлен позже.",
            "menu:upscale": "🔍 Апскейл — PRO/Anlas-связанная функция и будет добавлена следующим безопасным шагом.",
        }
        await call.message.edit_text(texts[call.data], reply_markup=main_menu())
    await call.answer()

@dp.callback_query(F.data.startswith("tool:"))
async def cb_prompt_tool(call: types.CallbackQuery):
    tool = call.data.split(":", 1)[1]
    s = get_settings(call.from_user.id)
    if not s.pending_prompt.strip():
        await call.answer("Сначала пришли промт", show_alert=True)
        return
    source = s.pending_original_prompt or s.pending_prompt
    if tool == "translate" and s.artraccoon_mode:
        prompt, original = prepare_prompt_for_user(call.from_user.id, source, force_tags=True)
    else:
        prompt = transform_prompt(source if tool == "translate" else s.pending_prompt, tool)
        original = source if tool == "translate" else s.pending_original_prompt
    updates = {"pending_prompt": prompt, "pending_original_prompt": original}
    if s.artraccoon_mode:
        updates["artraccoon_character_prompt"] = original
    s = patch_settings(call.from_user.id, **updates)
    preview = art_prompt_preview_text(s) if s.artraccoon_mode else prompt_preview_text(prompt, original, s)
    await call.message.edit_text(preview, parse_mode="HTML", reply_markup=prompt_menu_for(s, call.from_user.id))
    await call.answer("Промт обновлён")

def metadata_settings_updates(meta: dict) -> dict:
    updates = {}
    for key, target, cast in [
        ("width", "width", int), ("height", "height", int), ("steps", "steps", int),
        ("scale", "scale", float), ("cfg_rescale", "cfg_rescale", float), ("seed", "seed", int),
    ]:
        if key in meta:
            try:
                updates[target] = cast(meta[key])
            except (ValueError, TypeError):
                pass
    if str(meta.get("sampler", "")) in SAMPLERS:
        updates["sampler"] = str(meta["sampler"])
    if str(meta.get("uc_preset", "")) in UC_PRESETS:
        updates["uc_preset"] = str(meta["uc_preset"])
    if str(meta.get("noise_schedule", "")) in NOISE_SCHEDULES:
        updates["noise_schedule"] = str(meta["noise_schedule"])
    for key, target, cast in [
        ("qualityToggle", "add_quality_tags", lambda value: str(value).strip().lower() == "true" if isinstance(value, str) else bool(value)),
        ("variety_plus", "variety_plus", lambda value: str(value).strip().lower() == "true" if isinstance(value, str) else bool(value)),
        ("n_samples", "n_samples", int),
    ]:
        if key in meta:
            try:
                updates[target] = cast(meta[key])
            except (ValueError, TypeError):
                pass
    for name, value in MODELS.items():
        if meta.get("model") in (name, value):
            updates["model_name"] = name
            break
    if meta.get("negative_prompt"):
        updates["negative_prompt"] = str(meta["negative_prompt"])
    return updates

@dp.callback_query(F.data.startswith("meta:"))
async def cb_meta_apply(call: types.CallbackQuery):
    action = call.data.split(":", 1)[1]
    meta = get_last_metadata(call.from_user.id)
    if not meta:
        await call.answer("Metadata не найдена", show_alert=True)
        return
    if action == "show_settings":
        await call.message.answer(metadata_settings_summary(meta), parse_mode="HTML")
        await call.answer("Показываю настройки")
        return
    s = get_settings(call.from_user.id)
    updates = {}
    if action in {"base", "all"} and meta.get("prompt"):
        if s.artraccoon_mode:
            updates["artraccoon_base_prompt"] = str(meta["prompt"])
        else:
            updates["pending_prompt"] = str(meta["prompt"])
            updates["pending_original_prompt"] = ""
    if action in {"character", "all"} and meta.get("prompt"):
        character = str(meta["prompt"])
        if s.artraccoon_mode:
            updates["artraccoon_character_prompt"] = character
            updates["pending_original_prompt"] = character
            updates["pending_prompt"] = s.artraccoon_base_prompt
        else:
            updates["pending_original_prompt"] = character
            updates["pending_prompt"] = character
    if action in {"negative", "all"} and meta.get("negative_prompt"):
        if s.artraccoon_mode:
            updates["artraccoon_base_uc"] = str(meta["negative_prompt"])
        else:
            updates["negative_prompt"] = str(meta["negative_prompt"])
    if action in {"settings", "all"}:
        updates.update(metadata_settings_updates(meta))
    if not updates:
        await call.answer("В metadata нет подходящих полей для этого действия", show_alert=True)
        return
    patch_settings(call.from_user.id, **updates)
    await call.message.answer("✅ Metadata применена.", reply_markup=settings_markup_for(call.from_user.id))
    if "pending_prompt" in updates:
        await show_pending_prompt(call.message, call.from_user.id)
    await call.answer("Готово")

@dp.message(F.photo)
async def photo_message(message: types.Message):
    if message.from_user is None:
        return
    photo = message.photo[-1]
    file = await message.bot.get_file(photo.file_id)
    path = TMP_DIR / f"{message.from_user.id}_{message.message_id}.jpg"
    with path.open("wb") as f:
        await message.bot.download_file(file.file_path, destination=f)
    patch_settings(message.from_user.id, pending_image_path=str(path))
    await message.answer(
        "📎 Картинку сохранила для Img2Img. Теперь пришли промт обычным сообщением или командой /gen prompt.\n"
        "Силу можно менять в ⚙️ Настройки → 📎 Img2Img сила.",
        reply_markup=main_menu(),
    )

@dp.message(Command("cancel"))
async def cancel_cmd(message: types.Message, state: FSMContext):
    await state.clear()
    if message.from_user is not None:
        patch_settings(message.from_user.id, pending_prompt="", pending_original_prompt="", prompt_action="")
    await message.answer("Отменила ввод промта и очистила черновик.", reply_markup=main_menu())



@dp.callback_query(F.data.startswith("ar:edit:"))
async def cb_ar_edit(call: types.CallbackQuery, state: FSMContext):
    s = get_settings(call.from_user.id)
    if call.from_user.id not in ADMIN_IDS or not s.artraccoon_mode:
        await call.answer("Команда не найдена.", show_alert=True)
        return
    field = call.data.split(":", 2)[2]
    prompts = {
        "base": (GenState.waiting_ar_base, "📜 Пришли новый Base Prompt."),
        "base_uc": (GenState.waiting_ar_base_uc, "🚫 Пришли новый Base UC / базовый негатив."),
        "char_neg": (GenState.waiting_ar_char_neg, "👤 Пришли новый негатив персонажа."),
    }
    target = prompts.get(field)
    if not target:
        await call.answer("Неизвестное действие", show_alert=True)
        return
    await state.set_state(target[0])
    await call.message.answer(target[1])
    await call.answer()

@dp.callback_query(F.data == "ar:test")
async def cb_ar_test(call: types.CallbackQuery):
    s = get_settings(call.from_user.id)
    if call.from_user.id not in ADMIN_IDS or not s.artraccoon_mode:
        await call.answer("Команда не найдена.", show_alert=True)
        return
    await call.message.edit_text(art_prompt_preview_text(s), parse_mode="HTML", reply_markup=artraccoon_menu())
    await call.answer("Показываю сборку")

@dp.callback_query(F.data == "ar:exit")
async def cb_ar_exit(call: types.CallbackQuery):
    if call.from_user.id not in ADMIN_IDS:
        await call.answer("Команда не найдена.", show_alert=True)
        return
    patch_settings(call.from_user.id, artraccoon_mode=False, pending_prompt="", pending_original_prompt="", prompt_action="")
    await call.message.edit_text("🦝 ArtRaccoon режим выключен. Сохранённые настройки не удалены.", reply_markup=main_menu())
    await call.answer()

@dp.message(GenState.waiting_ar_base)
async def ar_base_input(message: types.Message, state: FSMContext):
    await state.clear()
    patch_settings(message.from_user.id, artraccoon_base_prompt=(message.text or "").strip())
    await message.answer("📜 Base Prompt сохранён.", reply_markup=artraccoon_menu())

@dp.message(GenState.waiting_ar_base_uc)
async def ar_base_uc_input(message: types.Message, state: FSMContext):
    await state.clear()
    patch_settings(message.from_user.id, artraccoon_base_uc=(message.text or "").strip())
    await message.answer("🚫 Base UC сохранён.", reply_markup=artraccoon_menu())

@dp.message(GenState.waiting_ar_char_neg)
async def ar_char_neg_input(message: types.Message, state: FSMContext):
    await state.clear()
    patch_settings(message.from_user.id, artraccoon_character_negative=(message.text or "").strip())
    await message.answer("👤 Негатив персонажа сохранён.", reply_markup=artraccoon_menu())

def parse_setting_value(user_id: int, field: str, raw: str) -> tuple[dict | None, str]:
    text = raw.strip()
    s = get_settings(user_id)
    advanced = (s.pro_mode and user_id in ADMIN_IDS) or s.artraccoon_mode
    try:
        if field == "size":
            m = re.fullmatch(r"\s*(\d{3,4})\s*[xх*]\s*(\d{3,4})\s*", text, re.I)
            if not m:
                return None, "Размер нужен в формате 832x1216."
            w, h = int(m.group(1)), int(m.group(2))
            if not advanced and (w, h) not in SAFE_RESOLUTIONS:
                return None, "В обычном режиме доступны только безопасные размеры: 512x768, 768x1344, 832x1216, 1024x1024, 1216x832."
            if not (256 <= w <= 2048 and 256 <= h <= 2048):
                return None, "Размер должен быть от 256 до 2048 по каждой стороне."
            return {"width": w, "height": h}, "📐 Размер обновлён."
        if field == "steps":
            val = int(text)
            if val < 1 or val > (60 if advanced else 28):
                return None, "В обычном режиме максимум 28 шагов." if not advanced else "Steps должны быть от 1 до 60."
            return {"steps": val}, "👣 Steps обновлены."
        if field == "scale":
            val = float(text.replace(",", "."))
            if not 0 <= val <= 20:
                return None, "CFG должен быть от 0 до 20."
            return {"scale": val}, "🧲 CFG обновлён."
        if field == "seed":
            if text.lower() == "random":
                return {"seed": -1}, "🎲 Seed переключён в random."
            val = int(text)
            if val < 0 or val > 4294967295:
                return None, "Seed должен быть от 0 до 4294967295 или random."
            return {"seed": val}, "🎲 Seed обновлён."
        if field == "negative":
            return {"negative_prompt": "" if text == "-" else text}, "🚫 Негатив обновлён."
        if field == "model":
            if text not in MODELS:
                return None, "Такой модели нет. Скопируй одно из названий из подсказки."
            return {"model_name": text}, "🧠 Модель обновлена."
        if field == "sampler":
            if text not in SAMPLERS:
                return None, "Такого sampler нет. Скопируй одно из значений из подсказки."
            return {"sampler": text}, "🎛 Sampler обновлён."
        if field == "n":
            val = int(text)
            if val not in (1, 2, 3, 4):
                return None, "Количество картинок: 1, 2, 3 или 4."
            if not advanced and val != 1:
                return None, "В обычном режиме количество картинок всегда 1."
            return {"n_samples": val}, "🖼 Количество обновлено."
        if field == "uc":
            if text not in UC_PRESETS:
                return None, "Такого UC-пресета нет. Скопируй одно из значений из подсказки."
            return {"uc_preset": text}, "🧪 UC-пресет обновлён."
        if field == "cfg":
            val = float(text.replace(",", "."))
            if not 0 <= val <= 1:
                return None, "CFG rescale должен быть от 0 до 1."
            return {"cfg_rescale": val}, "♻️ CFG rescale обновлён."
        if field == "noise":
            if text not in NOISE_SCHEDULES:
                return None, "Такого noise schedule нет. Скопируй одно из значений из подсказки."
            return {"noise_schedule": text}, "🌊 Noise schedule обновлён."
        if field == "img2img":
            strength, noise = [float(x.replace(",", ".")) for x in text.split("/", 1)]
            if not (0 <= strength <= 1 and 0 <= noise <= 1):
                return None, "Img2Img strength/noise должны быть от 0 до 1."
            return {"img2img_strength": strength, "img2img_noise": noise}, "📎 Img2Img обновлён."
    except (ValueError, TypeError):
        return None, "Не получилось прочитать значение. Проверь формат и попробуй ещё раз."
    return None, "Неизвестная настройка."

@dp.message(GenState.waiting_setting)
async def setting_text_input(message: types.Message, state: FSMContext):
    data = await state.get_data()
    field = data.get("setting_field", "")
    updates, response = parse_setting_value(message.from_user.id, field, message.text or "")
    if updates is None:
        await message.answer("😅 " + response + "\n\n" + SETTING_PROMPTS.get(field, ""), parse_mode="HTML", reply_markup=main_menu())
        return
    await state.clear()
    patch_settings(message.from_user.id, **updates)
    await message.answer(response, reply_markup=settings_markup_for(message.from_user.id))
    await message.answer(settings_text(message.from_user.id), reply_markup=settings_markup_for(message.from_user.id), parse_mode="HTML")

@dp.message(GenState.waiting_prompt)
async def gen_from_button(message: types.Message, state: FSMContext):
    prompt = message.text.strip() if message.text else ""

    if not prompt:
        await message.answer("Пришли текстовый промт или нажми /cancel.", reply_markup=main_menu())
        return

    await state.clear()
    converted, original = prepare_prompt_for_user(message.from_user.id, prompt)
    updates = {"pending_prompt": converted, "pending_original_prompt": original, "prompt_action": ""}
    if get_settings(message.from_user.id).artraccoon_mode:
        updates["artraccoon_character_prompt"] = original
    patch_settings(message.from_user.id, **updates)
    await show_pending_prompt(message, message.from_user.id)


@dp.message(Command("negative"))
async def negative_cmd(message: types.Message):
    text = message.text.replace("/negative", "", 1).strip()
    patch_settings(message.from_user.id, negative_prompt=text)
    await message.answer("🚫 Негативный промт обновлён.", reply_markup=settings_markup_for(message.from_user.id))

@dp.callback_query(F.data.startswith("set:model:"))
async def set_model(call: types.CallbackQuery):
    name = call.data.split(":", 2)[2]
    if name not in MODELS:
        await call.answer("Неизвестная модель", show_alert=True)
        return
    patch_settings(call.from_user.id, model_name=name)
    await call.message.edit_text(settings_text(call.from_user.id), reply_markup=settings_markup_for(call.from_user.id), parse_mode="HTML")
    await call.answer("Модель обновлена")

@dp.callback_query(F.data.startswith("set:size:"))
async def set_size(call: types.CallbackQuery):
    name = call.data.split(":", 2)[2]
    s = get_settings(call.from_user.id)
    if name == "swap":
        s.width, s.height = s.height, s.width
        save_settings(call.from_user.id, s)
    elif name in RESOLUTIONS:
        w, h = RESOLUTIONS[name]
        if not (s.pro_mode and call.from_user.id in ADMIN_IDS) and not s.artraccoon_mode and (w, h) not in SAFE_RESOLUTIONS:
            w, h = 832, 1216
            patch_settings(call.from_user.id, width=w, height=h)
            await call.message.edit_text(settings_text(call.from_user.id), reply_markup=settings_markup_for(call.from_user.id), parse_mode="HTML")
            await call.answer("В обычном режиме доступны только безопасные размеры. Поставила 832x1216 🙂", show_alert=True)
            return
        patch_settings(call.from_user.id, width=w, height=h)
    if not (s.pro_mode and call.from_user.id in ADMIN_IDS) and not s.artraccoon_mode and (get_settings(call.from_user.id).width, get_settings(call.from_user.id).height) not in SAFE_RESOLUTIONS:
        patch_settings(call.from_user.id, width=832, height=1216)
        await call.message.edit_text(settings_text(call.from_user.id), reply_markup=settings_markup_for(call.from_user.id), parse_mode="HTML")
        await call.answer("В обычном режиме доступны только безопасные размеры. Поставила 832x1216 🙂", show_alert=True)
        return
    await call.message.edit_text(settings_text(call.from_user.id), reply_markup=settings_markup_for(call.from_user.id), parse_mode="HTML")
    await call.answer("Размер обновлён")

@dp.callback_query(F.data.startswith("set:sampler:"))
async def set_sampler(call: types.CallbackQuery):
    if call.from_user.id not in ADMIN_IDS:
        await call.answer("💎 Эта функция временно отключена.", show_alert=True)
        return
    sampler = call.data.split(":", 2)[2]
    if sampler not in SAMPLERS:
        await call.answer("Неизвестный sampler", show_alert=True)
        return
    patch_settings(call.from_user.id, sampler=sampler)
    await call.message.edit_text(settings_text(call.from_user.id), reply_markup=settings_markup_for(call.from_user.id), parse_mode="HTML")
    await call.answer("Sampler обновлён")

@dp.callback_query(F.data.startswith("set:uc:"))
async def set_uc(call: types.CallbackQuery):
    if call.from_user.id not in ADMIN_IDS:
        await call.answer("💎 Эта функция временно отключена.", show_alert=True)
        return
    uc = call.data.split(":", 2)[2]
    if uc not in UC_PRESETS:
        await call.answer("Неизвестный UC preset", show_alert=True)
        return
    patch_settings(call.from_user.id, uc_preset=uc)
    await call.message.edit_text(settings_text(call.from_user.id), reply_markup=settings_markup_for(call.from_user.id), parse_mode="HTML")
    await call.answer("UC обновлён")

@dp.callback_query(F.data.startswith("set:n:"))
async def set_n(call: types.CallbackQuery):
    if call.from_user.id not in ADMIN_IDS:
        patch_settings(call.from_user.id, n_samples=1)
        await call.answer("💎 Эта функция временно отключена.", show_alert=True)
        return
    val = int(call.data.split(":", 2)[2])
    if val > 1 and not ((get_settings(call.from_user.id).pro_mode and call.from_user.id in ADMIN_IDS) or get_settings(call.from_user.id).artraccoon_mode):
        patch_settings(call.from_user.id, n_samples=1)
        await call.message.edit_text(settings_text(call.from_user.id), reply_markup=settings_markup_for(call.from_user.id), parse_mode="HTML")
        await call.answer(ANLAS_WARNING, show_alert=True)
        return
    patch_settings(call.from_user.id, n_samples=val)
    await call.message.edit_text(settings_text(call.from_user.id), reply_markup=settings_markup_for(call.from_user.id), parse_mode="HTML")
    await call.answer("Количество обновлено")

@dp.callback_query(F.data.startswith("set:steps:"))
async def set_steps(call: types.CallbackQuery):
    val = int(call.data.split(":", 2)[2])
    s = get_settings(call.from_user.id)
    if val > 28 and not (s.pro_mode and call.from_user.id in ADMIN_IDS) and not s.artraccoon_mode:
        val = 28
        patch_settings(call.from_user.id, steps=val)
        await call.message.edit_text(settings_text(call.from_user.id), reply_markup=settings_markup_for(call.from_user.id), parse_mode="HTML")
        await call.answer("В обычном режиме максимум 28 шагов. Аккуратно поставила 28 🙂", show_alert=True)
        return
    patch_settings(call.from_user.id, steps=val)
    await call.message.edit_text(settings_text(call.from_user.id), reply_markup=settings_markup_for(call.from_user.id), parse_mode="HTML")
    await call.answer("Steps обновлены")

@dp.callback_query(F.data.startswith("set:scale:"))
async def set_scale(call: types.CallbackQuery):
    val = float(call.data.split(":", 2)[2])
    patch_settings(call.from_user.id, scale=val)
    await call.message.edit_text(settings_text(call.from_user.id), reply_markup=settings_markup_for(call.from_user.id), parse_mode="HTML")
    await call.answer("Scale обновлён")

@dp.callback_query(F.data.startswith("set:seed:"))
async def set_seed(call: types.CallbackQuery):
    val = int(call.data.split(":", 2)[2])
    patch_settings(call.from_user.id, seed=val)
    await call.message.edit_text(settings_text(call.from_user.id), reply_markup=settings_markup_for(call.from_user.id), parse_mode="HTML")
    await call.answer("Seed обновлён")


@dp.callback_query(F.data.startswith("set:cfg:"))
async def set_cfg(call: types.CallbackQuery):
    if call.from_user.id not in ADMIN_IDS:
        await call.answer("💎 Эта функция временно отключена.", show_alert=True)
        return
    try:
        val = max(0.0, min(1.0, float(call.data.split(":", 2)[2])))
    except ValueError:
        await call.answer("Некорректное значение", show_alert=True)
        return
    patch_settings(call.from_user.id, cfg_rescale=val)
    await call.message.edit_text(settings_text(call.from_user.id), reply_markup=settings_markup_for(call.from_user.id), parse_mode="HTML")
    await call.answer("CFG rescale обновлён")

@dp.callback_query(F.data.startswith("set:noise:"))
async def set_noise(call: types.CallbackQuery):
    if call.from_user.id not in ADMIN_IDS:
        await call.answer("💎 Эта функция временно отключена.", show_alert=True)
        return
    val = call.data.split(":", 2)[2]
    if val not in NOISE_SCHEDULES:
        await call.answer("Неизвестный noise schedule", show_alert=True)
        return
    patch_settings(call.from_user.id, noise_schedule=val)
    await call.message.edit_text(settings_text(call.from_user.id), reply_markup=settings_markup_for(call.from_user.id), parse_mode="HTML")
    await call.answer("Noise schedule обновлён")

@dp.callback_query(F.data.startswith("set:img2img:"))
async def set_img2img(call: types.CallbackQuery):
    if call.from_user.id not in ADMIN_IDS:
        await call.answer("💎 Эта функция временно отключена.", show_alert=True)
        return
    raw = call.data.split(":", 2)[2]
    try:
        strength, noise = [float(x) for x in raw.split("/", 1)]
    except ValueError:
        await call.answer("Некорректное значение", show_alert=True)
        return
    patch_settings(call.from_user.id, img2img_strength=strength, img2img_noise=noise)
    await call.message.edit_text(settings_text(call.from_user.id), reply_markup=settings_markup_for(call.from_user.id), parse_mode="HTML")
    await call.answer("Img2Img обновлён")


@dp.callback_query(F.data == "toggle:pro")
async def toggle_pro(call: types.CallbackQuery):
    if call.from_user.id not in ADMIN_IDS:
        patch_settings(call.from_user.id, pro_mode=False)
        await call.answer("💎 Эта функция временно отключена.", show_alert=True)
        await call.message.answer("💎 Эта функция временно отключена.", reply_markup=main_menu())
        return
    s = get_settings(call.from_user.id)
    new_value = not s.pro_mode
    updates = {"pro_mode": new_value}
    if not new_value:
        updates["n_samples"] = 1
        if s.steps > 28:
            updates["steps"] = 28
        if (s.width, s.height) not in SAFE_RESOLUTIONS:
            updates.update({"width": 832, "height": 1216})
    patch_settings(call.from_user.id, **updates)
    await call.message.edit_text(settings_text(call.from_user.id), reply_markup=settings_markup_for(call.from_user.id), parse_mode="HTML")
    await call.answer("PRO / Анласы включены" if new_value else "Экономный режим включён")

@dp.callback_query(F.data == "toggle:furry")
async def toggle_furry(call: types.CallbackQuery):
    if call.from_user.id not in ADMIN_IDS:
        await call.answer("💎 Эта функция временно отключена.", show_alert=True)
        return
    s = get_settings(call.from_user.id)
    patch_settings(call.from_user.id, furry_mode=not s.furry_mode)
    s = get_settings(call.from_user.id)
    await call.message.edit_text("🦝 Режимы:", reply_markup=modes_menu(s.furry_mode, s.background_mode, s.add_quality_tags, s.variety_plus))
    await call.answer()

@dp.callback_query(F.data == "toggle:background")
async def toggle_background(call: types.CallbackQuery):
    if call.from_user.id not in ADMIN_IDS:
        await call.answer("💎 Эта функция временно отключена.", show_alert=True)
        return
    s = get_settings(call.from_user.id)
    patch_settings(call.from_user.id, background_mode=not s.background_mode)
    s = get_settings(call.from_user.id)
    await call.message.edit_text("🦝 Режимы:", reply_markup=modes_menu(s.furry_mode, s.background_mode, s.add_quality_tags, s.variety_plus))
    await call.answer()

@dp.callback_query(F.data == "toggle:quality")
async def toggle_quality(call: types.CallbackQuery):
    if call.from_user.id not in ADMIN_IDS:
        await call.answer("💎 Эта функция временно отключена.", show_alert=True)
        return
    s = get_settings(call.from_user.id)
    patch_settings(call.from_user.id, add_quality_tags=not s.add_quality_tags)
    s = get_settings(call.from_user.id)
    await call.message.edit_text("🦝 Режимы:", reply_markup=modes_menu(s.furry_mode, s.background_mode, s.add_quality_tags, s.variety_plus))
    await call.answer()

@dp.callback_query(F.data == "toggle:variety")
async def toggle_variety(call: types.CallbackQuery):
    if call.from_user.id not in ADMIN_IDS:
        await call.answer("💎 Эта функция временно отключена.", show_alert=True)
        return
    s = get_settings(call.from_user.id)
    patch_settings(call.from_user.id, variety_plus=not s.variety_plus)
    s = get_settings(call.from_user.id)
    await call.message.edit_text("🦝 Режимы:", reply_markup=modes_menu(s.furry_mode, s.background_mode, s.add_quality_tags, s.variety_plus))
    await call.answer("Variety+ обновлён")


def _is_admin(user_id: int) -> bool:
    return user_id in ADMIN_IDS


def _dictionary_stats_text() -> str:
    data = load_learned_dictionary()
    top = sorted(data["tag_frequency"].items(), key=lambda item: item[1], reverse=True)[:10]
    top_text = "\n".join(f"• {html.escape(tag)} — {count}" for tag, count in top) or "—"
    return (
        "📚 <b>Dictionary statistics</b>\n"
        f"Dictionary entries: <code>{len(data['ru_to_tags'])}</code>\n"
        f"Pending candidates: <code>{len(data['pending_suggestions'])}</code>\n"
        f"Rejected: <code>{len(data['rejected_tags'])}</code>\n\n"
        f"<b>Top learned tags</b>\n{top_text}"
    )


def _merge_dictionary_payload(payload: dict) -> int:
    current = load_learned_dictionary()
    incoming = payload if isinstance(payload, dict) else {}
    added = 0
    for ru, tags in incoming.get("ru_to_tags", {}).items():
        before = set(current["ru_to_tags"].get(str(ru).strip().lower(), []))
        add_learned_mapping(str(ru), tags if isinstance(tags, list) else [str(tags)])
        after = set(load_learned_dictionary()["ru_to_tags"].get(str(ru).strip().lower(), []))
        added += len(after - before)
    current = load_learned_dictionary()
    for tag, count in incoming.get("tag_frequency", {}).items():
        if parse_english_tags(str(tag)):
            current["tag_frequency"][str(tag).strip().lower()] = max(current["tag_frequency"].get(str(tag).strip().lower(), 0), int(count or 0))
    for tag in incoming.get("pending_suggestions", []):
        clean = parse_english_tags(str(tag))
        for item in clean:
            if item not in current["pending_suggestions"] and item not in current["rejected_tags"]:
                current["pending_suggestions"].append(item)
    for tag in incoming.get("rejected_tags", []):
        reject_tags(parse_english_tags(str(tag)))
    save_learned_dictionary(current)
    return added


@dp.message(Command("dict"))
async def dict_cmd(message: types.Message):
    if not message.from_user or not _is_admin(message.from_user.id):
        await message.answer("Только для администратора.")
        return
    await message.answer("📚 <b>Dictionary</b>", parse_mode="HTML", reply_markup=dictionary_menu())


@dp.callback_query(F.data == "dict:menu")
async def cb_dict_menu(call: types.CallbackQuery):
    if not _is_admin(call.from_user.id):
        await call.answer("Только админ", show_alert=True)
        return
    await call.message.answer("📚 <b>Dictionary</b>", parse_mode="HTML", reply_markup=dictionary_menu())
    await call.answer()


@dp.callback_query(F.data == "dict:stats")
async def cb_dict_stats(call: types.CallbackQuery):
    if not _is_admin(call.from_user.id):
        await call.answer("Только админ", show_alert=True)
        return
    await call.message.answer(_dictionary_stats_text(), parse_mode="HTML", reply_markup=dictionary_menu())
    await call.answer()


@dp.callback_query(F.data == "dict:pending")
async def cb_dict_pending(call: types.CallbackQuery):
    if not _is_admin(call.from_user.id):
        await call.answer("Только админ", show_alert=True)
        return
    tags = load_learned_dictionary()["pending_suggestions"]
    text = "🕓 <b>Pending candidates</b>\n" + ("\n".join(f"• {html.escape(t)}" for t in tags[:40]) or "—")
    await call.message.answer(text, parse_mode="HTML", reply_markup=dictionary_pending_menu(tags))
    await call.answer()


@dp.callback_query(F.data.startswith("dict_review:"))
async def cb_dict_review(call: types.CallbackQuery, state: FSMContext):
    if not _is_admin(call.from_user.id):
        await call.answer("Только админ", show_alert=True)
        return
    token = call.data.split(":", 1)[1]
    tags = moderation_candidates.get(token) or load_learned_dictionary()["pending_suggestions"][:20]
    if not tags:
        await call.answer("Нет кандидатов", show_alert=True)
        return
    await state.update_data(review_tags=tags, review_index=0)
    await state.set_state(GenState.waiting_dict_review_ru)
    await call.message.answer(f"Введите русское слово или фразу\nдля:\n\n<code>{html.escape(tags[0])}</code>", parse_mode="HTML")
    await call.answer()


@dp.callback_query(F.data.startswith("dict_reject:"))
async def cb_dict_reject(call: types.CallbackQuery):
    if not _is_admin(call.from_user.id):
        await call.answer("Только админ", show_alert=True)
        return
    tags = moderation_candidates.pop(call.data.split(":", 1)[1], [])
    reject_tags(tags)
    await call.message.answer("❌ Кандидаты отклонены.", reply_markup=dictionary_menu())
    await call.answer()


@dp.callback_query(F.data == "dict:reject_pending")
async def cb_dict_reject_pending(call: types.CallbackQuery):
    if not _is_admin(call.from_user.id):
        await call.answer("Только админ", show_alert=True)
        return
    tags = load_learned_dictionary()["pending_suggestions"]
    reject_tags(tags)
    await call.message.answer("❌ Все pending-кандидаты отклонены.", reply_markup=dictionary_menu())
    await call.answer()


@dp.callback_query(F.data.startswith("dict_one:"))
async def cb_dict_one(call: types.CallbackQuery, state: FSMContext):
    if not _is_admin(call.from_user.id):
        await call.answer("Только админ", show_alert=True)
        return
    tags = load_learned_dictionary()["pending_suggestions"]
    idx = int(call.data.split(":", 1)[1])
    if idx >= len(tags):
        await call.answer("Устарело", show_alert=True)
        return
    await state.update_data(review_tags=[tags[idx]], review_index=0)
    await state.set_state(GenState.waiting_dict_review_ru)
    await call.message.answer(f"Введите русское слово или фразу\nдля:\n\n<code>{html.escape(tags[idx])}</code>", parse_mode="HTML")
    await call.answer()


@dp.message(GenState.waiting_dict_review_ru)
async def dict_review_answer(message: types.Message, state: FSMContext):
    if not message.from_user or not _is_admin(message.from_user.id):
        return
    ru = (message.text or "").strip()
    data = await state.get_data()
    tags = data.get("review_tags", [])
    idx = int(data.get("review_index", 0))
    if not ru or idx >= len(tags):
        await state.clear()
        return
    add_learned_mapping(ru, [tags[idx]])
    idx += 1
    if idx < len(tags):
        await state.update_data(review_index=idx)
        await message.answer(f"Сохранено. Введите русское слово или фразу\nдля:\n\n<code>{html.escape(tags[idx])}</code>", parse_mode="HTML")
        return
    await state.clear()
    await message.answer("✅ Кандидаты добавлены в словарь.", reply_markup=dictionary_menu())


@dp.callback_query(F.data == "dict:add")
async def cb_dict_add(call: types.CallbackQuery, state: FSMContext):
    if not _is_admin(call.from_user.id):
        await call.answer("Только админ", show_alert=True)
        return
    await state.set_state(GenState.waiting_dict_ru)
    await call.message.answer("Введите русскую фразу")
    await call.answer()


@dp.message(GenState.waiting_dict_ru)
async def dict_add_ru(message: types.Message, state: FSMContext):
    await state.update_data(dict_ru=(message.text or "").strip())
    await state.set_state(GenState.waiting_dict_tags)
    await message.answer("Введите английские теги")


@dp.message(GenState.waiting_dict_tags)
async def dict_add_tags(message: types.Message, state: FSMContext):
    data = await state.get_data()
    add_learned_mapping(data.get("dict_ru", ""), parse_english_tags(message.text or ""))
    await state.clear()
    await message.answer("✅ Записано.", reply_markup=dictionary_menu())


@dp.callback_query(F.data == "dict:export")
async def cb_dict_export(call: types.CallbackQuery):
    if not _is_admin(call.from_user.id):
        await call.answer("Только админ", show_alert=True)
        return
    await call.message.answer_document(FSInputFile(DICTIONARY_PATH), caption="learned_dictionary.json")
    await call.answer()


@dp.callback_query(F.data == "dict:import")
async def cb_dict_import_hint(call: types.CallbackQuery):
    await call.message.answer("Пришлите JSON файлом и ответьте на него командой /dict_import.")
    await call.answer()


@dp.message(Command("dict_import"))
async def dict_import_cmd(message: types.Message):
    if not message.from_user or not _is_admin(message.from_user.id):
        await message.answer("Только для администратора.")
        return
    if not message.reply_to_message or not message.reply_to_message.document:
        await message.answer("Ответьте командой /dict_import на JSON-документ.")
        return
    file = await message.bot.get_file(message.reply_to_message.document.file_id)
    buf = BytesIO()
    await message.bot.download_file(file.file_path, destination=buf)
    try:
        payload = json.loads(buf.getvalue().decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError):
        await message.answer("❌ Некорректный JSON.")
        return
    added = _merge_dictionary_payload(payload)
    await message.answer(f"✅ Импорт завершён. Новых связей: {added}.", reply_markup=dictionary_menu())


@dp.callback_query(F.data == "dict:cleanup")
async def cb_dict_cleanup(call: types.CallbackQuery):
    if not _is_admin(call.from_user.id):
        await call.answer("Только админ", show_alert=True)
        return
    data = load_learned_dictionary()
    rejected = set(data["rejected_tags"])
    data["pending_suggestions"] = [tag for tag in data["pending_suggestions"] if tag not in rejected]
    save_learned_dictionary(data)
    await call.message.answer("🧹 Cleanup complete.", reply_markup=dictionary_menu())
    await call.answer()

@dp.message(F.text)
async def plain_text_prompt(message: types.Message):
    if message.from_user is None or not message.text:
        return
    text = message.text.strip()
    if not text:
        await message.answer("Пришли текстовый промт — я подготовлю черновик перед генерацией.", reply_markup=main_menu())
        return

    s = get_settings(message.from_user.id)
    if s.prompt_action == "append" and s.pending_prompt.strip():
        original = f"{(s.pending_original_prompt or s.pending_prompt).strip()}, {text}"
        prompt, stored_original = prepare_prompt_for_user(message.from_user.id, original)
        updates = {"pending_prompt": prompt, "pending_original_prompt": stored_original, "prompt_action": ""}
        if s.artraccoon_mode:
            updates["artraccoon_character_prompt"] = stored_original
        patch_settings(message.from_user.id, **updates)
        await message.answer("✏️ Добавила текст к черновику.")
    else:
        converted, original = prepare_prompt_for_user(message.from_user.id, text)
        updates = {"pending_prompt": converted, "pending_original_prompt": original, "prompt_action": ""}
        if s.artraccoon_mode:
            updates["artraccoon_character_prompt"] = original
        patch_settings(message.from_user.id, **updates)
        if s.prompt_action == "replace":
            await message.answer("🔁 Заменила черновик новым промтом.")

    await show_pending_prompt(message, message.from_user.id)

async def main():
    global bot

    if not BOT_TOKEN:
        raise RuntimeError("BOT_TOKEN не заполнен в .env")

    if PROXY_URL:
        log.info("Telegram proxy enabled")
        session = AiohttpSession(proxy=PROXY_URL)
        bot = Bot(token=BOT_TOKEN, session=session)
    else:
        log.info("Telegram proxy disabled")
        bot = Bot(token=BOT_TOKEN)

    await dp.start_polling(bot)

if __name__ == "__main__":
    asyncio.run(main())
