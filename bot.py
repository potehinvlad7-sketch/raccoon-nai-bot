import asyncio
import logging
import os
import html
import json
import re
from io import BytesIO
from pathlib import Path
from datetime import datetime, timezone

from aiogram import BaseMiddleware, Bot, Dispatcher, F, types
from aiogram.filters import Command
from aiogram.exceptions import TelegramBadRequest, TelegramForbiddenError
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import BufferedInputFile, FSInputFile, InlineKeyboardButton, InlineKeyboardMarkup
from aiogram.client.session.aiohttp import AiohttpSession
from dotenv import load_dotenv

from config_defaults import QUICK_PRESETS, RESOLUTIONS, MODELS, SAMPLERS, UC_PRESETS, NOISE_SCHEDULES, AELITA_DESCRIPTION, UserSettings, MAX_EXTRA_CHARACTERS
from keyboards import (
    main_menu as base_main_menu, settings_menu, modes_menu, presets_menu, pending_prompt_menu,
    after_generation_menu, generation_item_menu, artraccoon_menu, meta_import_menu, confirm_reset_menu, model_menu, size_menu, sampler_menu, uc_menu, noise_menu, seed_menu, samples_menu, moderation_dictionary_menu, dictionary_menu, dictionary_pending_menu, admin_panel_menu,
    admin_ar_vibe_menu, admin_nai_debug_menu, admin_site_clone_menu, registry_fields_text, admin_purchases_menu, admin_users_menu, admin_broadcast_menu, admin_broadcast_confirm_menu, characters_menu,
)
from app.services.nai_client import (
    NovelAIClient, NovelAIError, sanitize_payload,
    SITE_MODE_STEPS, SITE_MODE_SCALE, SITE_MODE_CFG_RESCALE, SITE_MODE_SAMPLER, SITE_MODE_NOISE_SCHEDULE,
)
from prompt_tools import (
    DICTIONARY_PATH, add_learned_mapping, learn_from_english_prompt,
    load_learned_dictionary, parse_english_tags, reject_tags,
    looks_like_english_tags, save_learned_dictionary
)
from storage import (
    get_settings, save_settings, patch_settings, add_history, get_history,
    add_favorite, get_favorites, delete_favorite, set_last_metadata, get_last_metadata,
    set_last_payload, get_last_payload, get_config_value, set_config_value, delete_config_value,
    load_all_users_for_admin_stats, get_user_record_for_admin, adjust_paid_generations_balance, clear_user_draft_for_admin, update_user_identity
)

from services.generation import (
    DAILY_GENERATION_LIMIT, GENERATION_TIMEOUT_SECONDS, SAFE_RESOLUTIONS, apply_anlas_safe_defaults as _apply_anlas_safe_defaults,
    ar_payload_mode as _ar_payload_mode, artraccoon_prompt_defaults, assemble_ar_prompt, cooldown_remaining as _cooldown_remaining,
    mark_generation_started as _mark_generation_started, remaining_generations as _remaining_generations,
    basic_defaults_from_settings, factory_basic_defaults, safe_existing_generated_path, safe_generation_defaults, saved_basic_defaults, sanitize_basic_defaults, save_generated_images,
)
from services.metadata import (
    metadata_settings_summary, metadata_summary, nai_compare_summary_text, parse_nai_metadata,
)
from app.nai.settings_registry import settings_updates_from_metadata
from ui.texts import (
    CANCEL_TEXT, CLEAR_TEXT, DAILY_LIMIT_TEXT, EDIT_PROMPT_TEXT, GENERATION_STARTED_TEXT,
    PAID_PLACEHOLDER_TEXT, PROMPT_EMPTY_TEXT, cooldown_text, generation_result_caption,
    howto_text as branded_howto_text, main_menu_text, nai_payload_summary_text,
    presets_text, prompt_preview_text, prompt_request_text, start_text,
)

load_dotenv()

BOT_TOKEN = os.getenv("BOT_TOKEN", "")
NOVELAI_TOKEN = (os.getenv("NOVELAI_TOKEN") or os.getenv("NAI_TOKEN") or "").strip()
NAI_MODEL = os.getenv("NAI_MODEL", "").strip()
PROXY_URL = os.getenv("PROXY_URL", "socks5://127.0.0.1:1080").strip()
CHANNEL_URL = os.getenv("CHANNEL_URL", "").strip()
SUPPORT_GROUP_ID_RAW = os.getenv("SUPPORT_GROUP_ID", "").strip()
SUPPORT_GROUP_ID = int(SUPPORT_GROUP_ID_RAW) if SUPPORT_GROUP_ID_RAW.lstrip("-").isdigit() else None
SUPPORT_URL = os.getenv("SUPPORT_URL", "").strip()

ADMIN_IDS = [
    int(x.strip())
    for x in os.getenv("ADMIN_IDS", "").split(",")
    if x.strip().isdigit()
]

logging.basicConfig(level=logging.INFO)
log = logging.getLogger("novelai_tg_bot")

bot: Bot | None = None
dp = Dispatcher()


class UserIdentityMiddleware(BaseMiddleware):
    async def __call__(self, handler, event, data):
        user = getattr(event, "from_user", None)
        update_user_identity(user)
        return await handler(event, data)


dp.message.middleware(UserIdentityMiddleware())
dp.callback_query.middleware(UserIdentityMiddleware())
nai = NovelAIClient(NOVELAI_TOKEN, default_model=NAI_MODEL, proxy_url=PROXY_URL)


def ar_payload_mode(s) -> str:
    return _ar_payload_mode(s, NAI_MODEL)


def remaining_generations(user_id: int) -> int | None:
    return _remaining_generations(user_id, ADMIN_IDS)


def mark_generation_started(user_id: int) -> None:
    _mark_generation_started(user_id, ADMIN_IDS)


def cooldown_remaining(user_id: int) -> int:
    return _cooldown_remaining(user_id, ADMIN_IDS)


def apply_anlas_safe_defaults(user_id: int):
    return _apply_anlas_safe_defaults(user_id, ADMIN_IDS)


def is_advanced_user(user_id: int) -> bool:
    s = get_settings(user_id)
    return user_id in ADMIN_IDS and (s.pro_mode or s.artraccoon_mode)


def artraccoon_vibe_prompt() -> str:
    return str(get_config_value("artraccoon_vibe_prompt", "") or "").strip()


def visible_prompt_with_optional_vibe(user_id: int, visible_prompt: str) -> tuple[str, bool]:
    s = get_settings(user_id)
    vibe = artraccoon_vibe_prompt()
    if s.artraccoon_vibe_enabled and vibe:
        return f"{vibe}, {visible_prompt}" if visible_prompt.strip() else vibe, True
    return visible_prompt, False

class GenState(StatesGroup):
    waiting_prompt = State()
    waiting_ar_base = State()
    waiting_ar_base_uc = State()
    waiting_ar_char_neg = State()
    waiting_setting = State()
    waiting_dict_ru = State()
    waiting_dict_tags = State()
    waiting_dict_review_ru = State()
    waiting_ar_vibe = State()
    waiting_purchase_user_id = State()
    waiting_purchase_amount = State()
    waiting_admin_user_id = State()
    waiting_broadcast_text = State()
    waiting_basic_steps = State()
    waiting_basic_cfg = State()
    waiting_basic_negative = State()
    waiting_char_prompt = State()
    waiting_char_uc = State()
    waiting_char_position = State()
    waiting_support_message = State()
    waiting_admin_reply = State()

def main_menu():
    return base_main_menu(CHANNEL_URL)


SUPPORT_PROMPT_TEXT = (
    "Напиши сообщение для администрации.\n\n"
    "Можно отправить:\n"
    "• текст\n"
    "• фотографию\n"
    "• документ\n"
    "• видео\n"
    "• голосовое сообщение\n\n"
    "Для отмены используй /cancel"
)

DONATE_TEXT = (
    "Спасибо за желание поддержать развитие Raccoon NAI Bot ❤️\n\n"
    "Каждый донат помогает оплачивать серверы, улучшать функционал и добавлять новые возможности."
)

SUPPORTED_SUPPORT_CONTENT_TYPES = {"text", "photo", "document", "animation", "video", "audio", "voice", "sticker"}


def donate_menu() -> InlineKeyboardMarkup:
    buttons = []
    if SUPPORT_URL:
        buttons.append([InlineKeyboardButton(text="💖 Поддержать проект", url=SUPPORT_URL)])
    buttons.append([InlineKeyboardButton(text="🏠 Главное меню", callback_data="menu:main")])
    return InlineKeyboardMarkup(inline_keyboard=buttons)


def admin_support_menu(user_id: int) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[[
        InlineKeyboardButton(text="✉️ Ответить", callback_data=f"support_reply:{user_id}"),
        InlineKeyboardButton(text="❌ Закрыть", callback_data="support_close"),
    ]])


def support_identity_text(user: types.User, message: types.Message) -> str:
    full_name = user.full_name or "—"
    username = f"@{user.username}" if user.username else "—"
    date = message.date.astimezone(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC") if message.date else datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")
    return (
        "📩 <b>Новое обращение</b>\n\n"
        f"👤 <b>Имя:</b>\n{html.escape(full_name)}\n\n"
        f"🔗 <b>Username:</b>\n{html.escape(username)}\n\n"
        f"🆔 <b>ID:</b>\n<code>{user.id}</code>\n\n"
        f"🕒 <b>Date:</b>\n<code>{html.escape(date)}</code>"
    )


def _caption(prefix: str, message: types.Message) -> str:
    original = message.caption or ""
    return prefix if not original else f"{prefix}\n\n{original}"


async def send_support_content(bot: Bot, chat_id: int, message: types.Message, *, prefix: str = "", reply_markup: InlineKeyboardMarkup | None = None) -> None:
    content_type = message.content_type
    if content_type == "text":
        text = message.text or ""
        await bot.send_message(chat_id, f"{prefix}\n\n{text}" if prefix else text, reply_markup=reply_markup)
    elif content_type == "photo":
        await bot.send_photo(chat_id, message.photo[-1].file_id, caption=_caption(prefix, message) if prefix else message.caption, reply_markup=reply_markup)
    elif content_type == "document":
        await bot.send_document(chat_id, message.document.file_id, caption=_caption(prefix, message) if prefix else message.caption, reply_markup=reply_markup)
    elif content_type == "animation":
        await bot.send_animation(chat_id, message.animation.file_id, caption=_caption(prefix, message) if prefix else message.caption, reply_markup=reply_markup)
    elif content_type == "video":
        await bot.send_video(chat_id, message.video.file_id, caption=_caption(prefix, message) if prefix else message.caption, reply_markup=reply_markup)
    elif content_type == "audio":
        await bot.send_audio(chat_id, message.audio.file_id, caption=_caption(prefix, message) if prefix else message.caption, reply_markup=reply_markup)
    elif content_type == "voice":
        await bot.send_voice(chat_id, message.voice.file_id, caption=_caption(prefix, message) if prefix else message.caption, reply_markup=reply_markup)
    elif content_type == "sticker":
        if prefix:
            await bot.send_message(chat_id, prefix)
        await bot.send_sticker(chat_id, message.sticker.file_id, reply_markup=reply_markup)
    else:
        raise ValueError(f"Unsupported support content type: {content_type}")


async def start_support_flow(message: types.Message, state: FSMContext) -> None:
    await state.set_state(GenState.waiting_support_message)
    await message.answer(SUPPORT_PROMPT_TEXT, reply_markup=main_menu())


async def send_donate_message(message: types.Message) -> None:
    if not SUPPORT_URL:
        await message.answer("Ссылка для поддержки проекта пока не настроена.", reply_markup=main_menu())
        return
    await message.answer(DONATE_TEXT, reply_markup=donate_menu())


def basic_defaults_menu() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [
            InlineKeyboardButton(text="🧠 Модель", callback_data="basic_defaults:choose_model"),
            InlineKeyboardButton(text="📐 Размер", callback_data="basic_defaults:choose_size"),
        ],
        [
            InlineKeyboardButton(text="👣 Шаги", callback_data="basic_defaults:ask_steps"),
            InlineKeyboardButton(text="🧲 CFG", callback_data="basic_defaults:ask_cfg"),
        ],
        [
            InlineKeyboardButton(text="🧪 Sampler", callback_data="basic_defaults:choose_sampler"),
            InlineKeyboardButton(text="🧯 Негатив", callback_data="basic_defaults:ask_negative"),
        ],
        [InlineKeyboardButton(text="✨ Quality tags ON/OFF", callback_data="basic_defaults:toggle_quality")],
        [InlineKeyboardButton(text="🦝 Variety+ ON/OFF", callback_data="basic_defaults:toggle_variety")],
        [InlineKeyboardButton(text="💾 Сохранить мои настройки", callback_data="basic_defaults:save")],
        [InlineKeyboardButton(text="♻️ Сбросить", callback_data="basic_defaults:reset")],
        [InlineKeyboardButton(text="👁 Показать подробно", callback_data="basic_defaults:show")],
        [InlineKeyboardButton(text="🧪 Тест", callback_data="basic_defaults:test")],
        [InlineKeyboardButton(text="⬅️ Назад в админку", callback_data="admin:menu")],
    ])


def basic_defaults_select_menu(kind: str) -> InlineKeyboardMarkup:
    if kind == "model":
        buttons = [InlineKeyboardButton(text=name, callback_data=f"basic_defaults:set_model:{name}") for name in MODELS]
    elif kind == "size":
        buttons = [InlineKeyboardButton(text=name, callback_data=f"basic_defaults:set_size:{name}") for name, size in RESOLUTIONS.items() if size in SAFE_RESOLUTIONS]
    elif kind == "sampler":
        buttons = [InlineKeyboardButton(text=name, callback_data=f"basic_defaults:set_sampler:{name}") for name in SAMPLERS]
    else:
        buttons = []
    keyboard = [buttons[i:i + 1] for i in range(0, len(buttons), 1)]
    keyboard.append([InlineKeyboardButton(text="⬅️ Назад", callback_data="admin:basic_defaults")])
    return InlineKeyboardMarkup(inline_keyboard=keyboard)


def basic_defaults_text(defaults: dict | None = None, *, saved: bool | None = None) -> str:
    defaults = sanitize_basic_defaults(defaults if defaults is not None else get_config_value("basic_generation_defaults", None), clamp_steps=True)
    negative = "задан" if str(defaults.get("negative_prompt") or "").strip() else "пусто"
    return (
        "⚙️ <b>Дефолты обычного режима</b>\n\n"
        "Эти настройки будут использоваться у всех обычных пользователей.\n\n"
        f"🧠 Модель: {html.escape(str(defaults['model_name']))}\n"
        f"📐 Размер: {defaults['width']}×{defaults['height']}\n"
        f"👣 Шаги: {defaults['steps']}\n"
        f"🧲 CFG: {defaults['scale']}\n"
        "🎲 Seed: случайный\n"
        f"🧪 Sampler: {html.escape(str(defaults['sampler']))}\n"
        f"🧯 Негатив: {negative}\n"
        f"✨ Quality tags: {'ВКЛ' if defaults['add_quality_tags'] else 'ВЫКЛ'}\n"
        f"🦝 Variety+: {'ВКЛ' if defaults['variety_plus'] else 'ВЫКЛ'}"
    )


def basic_defaults_details_text(defaults: dict | None = None, *, saved: bool | None = None) -> str:
    defaults = sanitize_basic_defaults(defaults if defaults is not None else get_config_value("basic_generation_defaults", None), clamp_steps=True)
    if saved is None:
        saved = isinstance(get_config_value("basic_generation_defaults", None), dict)
    return (
        "🧰 <b>Технические значения дефолтов</b>\n"
        f"Source: <code>{'saved config' if saved else 'factory defaults'}</code>\n\n"
        f"model_name: <code>{html.escape(str(defaults['model_name']))}</code>\n"
        f"size: <code>{defaults['width']}x{defaults['height']}</code>\n"
        f"steps: <code>{defaults['steps']}</code>\n"
        f"scale: <code>{defaults['scale']}</code>\n"
        f"sampler: <code>{html.escape(str(defaults['sampler']))}</code>\n"
        f"uc_preset: <code>{html.escape(str(defaults['uc_preset']))}</code>\n"
        f"cfg_rescale: <code>{defaults['cfg_rescale']}</code>\n"
        f"noise_schedule: <code>{html.escape(str(defaults['noise_schedule']))}</code>\n"
        f"negative_prompt: <code>{html.escape(defaults['negative_prompt'] or '—')}</code>\n"
        f"add_quality_tags: <code>{defaults['add_quality_tags']}</code>\n"
        f"variety_plus: <code>{defaults['variety_plus']}</code>\n"
        "n_samples: <code>1 (forced)</code>\n"
        "seed: <code>random (-1)</code>"
    )



def update_basic_defaults_config(**updates) -> dict:
    defaults = saved_basic_defaults()
    defaults.update(updates)
    defaults = sanitize_basic_defaults(defaults, clamp_steps=True)
    defaults["steps"] = min(28, max(1, int(defaults.get("steps", 28))))
    defaults["scale"] = min(10.0, max(1.0, float(defaults.get("scale", 8.0))))
    defaults["n_samples"] = 1
    defaults["seed"] = -1
    set_config_value("basic_generation_defaults", defaults)
    return defaults


def parse_basic_steps(raw: str) -> int:
    return min(28, max(1, int((raw or "").strip())))


def parse_basic_cfg(raw: str) -> float:
    return min(10.0, max(1.0, float((raw or "").strip().replace(",", "."))))

def settings_from_basic_defaults() -> UserSettings:
    base = UserSettings()
    for key, value in saved_basic_defaults().items():
        if hasattr(base, key):
            setattr(base, key, value)
    return base

PAID_PLACEHOLDER = PAID_PLACEHOLDER_TEXT
ANLAS_WARNING = PAID_PLACEHOLDER
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


def user_label(user: types.User) -> str:
    username = f"@{user.username}" if user.username else "@-"
    name = " ".join(x for x in [user.first_name, user.last_name] if x) or "-"
    return f"id={user.id} / {username} / {name}"

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

async def send_moderation_copy(bot_obj: Bot, user: types.User, original_prompt: str, final_prompt: str, s, candidates: list[str] | None = None, hidden_vibe_applied: bool = False) -> None:
    if user.id in ADMIN_IDS:
        return
    text = moderation_summary(user, original_prompt, final_prompt, s, candidates)
    if hidden_vibe_applied:
        text += "\n\n<blockquote expandable>🦝 Hidden ArtRaccoon vibe applied</blockquote>"
    token = f"{user.id}:{int(datetime.now(timezone.utc).timestamp())}"
    if candidates:
        moderation_candidates[token] = list(dict.fromkeys(candidates))
    markup = moderation_dictionary_menu(token) if candidates else None
    for admin_id in ADMIN_IDS:
        try:
            await bot_obj.send_message(admin_id, text, parse_mode="HTML", reply_markup=markup)
        except Exception:
            log.exception("Failed to send moderation prompt summary to admin %s", admin_id)

async def send_moderation_image(bot_obj: Bot, user: types.User, img: bytes, original_prompt: str, final_prompt: str, s, idx: int, candidates: list[str] | None = None, hidden_vibe_applied: bool = False) -> None:
    if user.id in ADMIN_IDS:
        return
    caption = f"🛡 <b>Готовое изображение</b>\n👤 <code>{html.escape(user_label(user))}</code>\n📐 <code>{s.width}x{s.height}</code> · 🎲 <code>{'random' if s.seed == -1 else s.seed}</code>"
    if hidden_vibe_applied:
        caption += "\n<blockquote expandable>🦝 Hidden ArtRaccoon vibe applied</blockquote>"
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
        await message.answer(PROMPT_EMPTY_TEXT, reply_markup=main_menu())
        return
    preview = art_prompt_preview_text(s) if s.artraccoon_mode else prompt_preview_text(s.pending_prompt, s.pending_original_prompt, s, remaining_generations(user_id))
    await message.answer(
        preview,
        parse_mode="HTML",
        reply_markup=pending_prompt_menu(bool(s.pending_image_path), is_advanced_user(user_id), compact=s.artraccoon_mode, vibe_enabled=s.artraccoon_vibe_enabled, vibe_available=bool(artraccoon_vibe_prompt())),
    )


def howto_text(user_id: int | None = None) -> str:
    remaining = remaining_generations(user_id) if user_id is not None else None
    return branded_howto_text(remaining, DAILY_GENERATION_LIMIT)

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
    return settings_menu(is_advanced_user(user_id), show_pro_button=user_id in ADMIN_IDS)

def prompt_menu_for(s, user_id: int):
    return pending_prompt_menu(bool(s.pending_image_path), is_advanced_user(user_id), compact=s.artraccoon_mode, vibe_enabled=s.artraccoon_vibe_enabled, vibe_available=bool(artraccoon_vibe_prompt()))

def prepare_prompt_for_user(user_id: int, text: str, force_tags: bool = False) -> tuple[str, str]:
    s = get_settings(user_id)
    if s.artraccoon_mode:
        return s.artraccoon_base_prompt, text
    return text, ""


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
    await generate_image_from_prompt(message, s.last_prompt, actor=user)

@dp.message(Command("start"))
async def start(message: types.Message):
    get_settings(message.from_user.id)
    await message.answer(
        start_text(remaining_generations(message.from_user.id), DAILY_GENERATION_LIMIT, message.from_user.id in ADMIN_IDS),
        reply_markup=main_menu(),
        parse_mode="HTML",
    )

@dp.message(Command("help", "howto"))
async def help_cmd(message: types.Message):
    await message.answer(howto_text(message.from_user.id), reply_markup=main_menu(), parse_mode="HTML")

def admin_panel_text() -> str:
    return "🛠 <b>Админ-панель RaccoonNAI</b>"


def _parse_dt(raw) -> datetime | None:
    if not isinstance(raw, str) or not raw.strip():
        return None
    try:
        return datetime.fromisoformat(raw.replace("Z", "+00:00"))
    except ValueError:
        return None


def build_admin_stats() -> dict:
    users = load_all_users_for_admin_stats()
    now = datetime.now(timezone.utc)
    today = now.date()
    week_ago = now.timestamp() - 7 * 24 * 60 * 60
    total_generations = generations_today = free_used = 0
    active_today: set[str] = set()
    active_7d: set[str] = set()
    total_paid_balance = total_favorites = total_history = 0
    vibe_users = advanced_users = pending_drafts = 0
    by_user: list[tuple[str, int]] = []
    model_counts: dict[str, int] = {}
    size_counts: dict[str, int] = {}
    last_ts: datetime | None = None
    for uid, raw in users.items():
        user = raw if isinstance(raw, dict) else {}
        history = user.get("history", []) if isinstance(user.get("history", []), list) else []
        favorites = user.get("favorites", []) if isinstance(user.get("favorites", []), list) else []
        count = len(history)
        total_generations += count
        total_history += count
        total_favorites += len(favorites)
        free_used += int(user.get("free_daily_used", user.get("daily_generation_count", 0)) or 0)
        total_paid_balance += int(user.get("paid_generations_balance", 0) or 0)
        if user.get("artraccoon_vibe_enabled"):
            vibe_users += 1
        if user.get("pro_mode") or user.get("artraccoon_mode") or uid.isdigit() and int(uid) in ADMIN_IDS:
            advanced_users += 1
        if str(user.get("pending_prompt") or "").strip():
            pending_drafts += 1
        by_user.append((uid, count))
        for item in history:
            if not isinstance(item, dict):
                continue
            dt = _parse_dt(item.get("timestamp"))
            if dt:
                if dt.tzinfo is None:
                    dt = dt.replace(tzinfo=timezone.utc)
                if dt.date() == today:
                    generations_today += 1
                    active_today.add(uid)
                if dt.timestamp() >= week_ago:
                    active_7d.add(uid)
                if last_ts is None or dt > last_ts:
                    last_ts = dt
            model = str(item.get("model") or "").strip()
            size = str(item.get("size") or "").strip()
            if model:
                model_counts[model] = model_counts.get(model, 0) + 1
            if size:
                size_counts[size] = size_counts.get(size, 0) + 1
    total_users = len(users)
    return {
        "total_users": total_users, "users_with_username": sum(1 for raw in users.values() if isinstance(raw, dict) and str(raw.get("username") or "").strip()), "users_without_username": sum(1 for raw in users.values() if not (isinstance(raw, dict) and str(raw.get("username") or "").strip())), "total_generations": total_generations, "generations_today": generations_today,
        "active_today": len(active_today), "active_7d": len(active_7d), "free_used": free_used,
        "paid_used": None, "paid_balance": total_paid_balance, "vibe_users": vibe_users, "advanced_users": advanced_users,
        "pending_drafts": pending_drafts, "favorites": total_favorites, "history": total_history,
        "avg": total_generations / total_users if total_users else 0,
        "top_users": sorted(by_user, key=lambda x: x[1], reverse=True)[:10],
        "top_models": sorted(model_counts.items(), key=lambda x: x[1], reverse=True)[:5],
        "top_sizes": sorted(size_counts.items(), key=lambda x: x[1], reverse=True)[:5],
        "last_ts": last_ts.isoformat() if last_ts else "—",
    }


def format_admin_stats(stats: dict) -> str:
    top_users = "\n".join(f"• <code>{html.escape(uid)}</code> — {count}" for uid, count in stats["top_users"] if count) or "—"
    top_models = "\n".join(f"• <code>{html.escape(k)}</code> — {v}" for k, v in stats["top_models"]) or "—"
    top_sizes = "\n".join(f"• <code>{html.escape(k)}</code> — {v}" for k, v in stats["top_sizes"]) or "—"
    return (
        "📊 <b>Статистика RaccoonNAI</b>\n\n"
        f"👥 Total users: <code>{stats['total_users']}</code>\n"
        f"🔗 Users with username: <code>{stats['users_with_username']}</code>\n"
        f"🚫 Users without username: <code>{stats['users_without_username']}</code>\n"
        f"🖼 Total generations: <code>{stats['total_generations']}</code>\n"
        f"📅 Generations today: <code>{stats['generations_today']}</code>\n"
        f"🔥 Active users today: <code>{stats['active_today']}</code>\n"
        f"📈 Active users last 7 days: <code>{stats['active_7d']}</code>\n"
        f"🎁 Free generations used: <code>{stats['free_used']}</code>\n"
        "💎 Paid generations used: <code>not implemented</code>\n"
        f"📦 Total remaining paid generation balance: <code>{stats['paid_balance']}</code>\n"
        f"🦝 Users with ArtRaccoon Vibe enabled: <code>{stats['vibe_users']}</code>\n"
        f"⚙️ Users with advanced/admin mode enabled: <code>{stats['advanced_users']}</code>\n"
        f"🧪 Users with saved pending drafts: <code>{stats['pending_drafts']}</code>\n"
        f"⭐ Total favorites: <code>{stats['favorites']}</code>\n"
        f"🕘 Total history items: <code>{stats['history']}</code>\n"
        f"📊 Average generations per user: <code>{stats['avg']:.2f}</code>\n"
        f"🕒 Last generation timestamp: <code>{html.escape(stats['last_ts'])}</code>\n\n"
        f"<b>Top 10 users by generations</b>\n{top_users}\n\n"
        f"<b>Top models used</b>\n{top_models}\n\n"
        f"<b>Top sizes used</b>\n{top_sizes}"
    )


async def show_admin_panel(message: types.Message) -> None:
    if not message.from_user or message.from_user.id not in ADMIN_IDS:
        await message.answer("Команда не найдена.")
        return
    await message.answer(admin_panel_text(), parse_mode="HTML", reply_markup=admin_panel_menu())



@dp.message(Command("chat_id"))
async def chat_id_cmd(message: types.Message):
    await message.answer(
        f"Chat ID: <code>{message.chat.id}</code>\n"
        f"Chat type: <code>{html.escape(message.chat.type)}</code>",
        parse_mode="HTML",
    )


@dp.message(Command("support"))
async def support_cmd(message: types.Message, state: FSMContext):
    log.info("support message received: support flow started by user %s", message.from_user.id if message.from_user else "unknown")
    await start_support_flow(message, state)


@dp.callback_query(F.data == "support:start")
async def cb_support_start(call: types.CallbackQuery, state: FSMContext):
    log.info("support message received: support flow started by user %s", call.from_user.id)
    await state.set_state(GenState.waiting_support_message)
    await call.message.answer(SUPPORT_PROMPT_TEXT, reply_markup=main_menu())
    await call.answer()


@dp.message(Command("donate"))
async def donate_cmd(message: types.Message):
    await send_donate_message(message)


@dp.callback_query(F.data == "donate:open")
async def cb_donate(call: types.CallbackQuery):
    await call.message.answer(DONATE_TEXT, reply_markup=donate_menu())
    await call.answer()


@dp.message(GenState.waiting_support_message)
async def support_message_input(message: types.Message, state: FSMContext):
    if message.from_user is None:
        return
    if message.content_type not in SUPPORTED_SUPPORT_CONTENT_TYPES:
        await message.answer("Не могу отправить этот тип сообщения. Пришли текст, фото, документ, видео, аудио, голосовое или стикер.\n\nДля отмены используй /cancel")
        return

    log.info("support message received from user %s type=%s", message.from_user.id, message.content_type)
    admin_markup = admin_support_menu(message.from_user.id)
    delivered_admins = 0
    for admin_id in ADMIN_IDS:
        try:
            await message.bot.send_message(admin_id, support_identity_text(message.from_user, message), parse_mode="HTML")
            await send_support_content(message.bot, admin_id, message, reply_markup=admin_markup)
            delivered_admins += 1
        except Exception:
            log.exception("support errors: failed to deliver support message to admin %s", admin_id)

    if SUPPORT_GROUP_ID is None:
        log.warning("support errors: SUPPORT_GROUP_ID is not configured; skipping anonymous group delivery")
    else:
        try:
            await send_support_content(message.bot, SUPPORT_GROUP_ID, message, prefix="📩 Анонимное обращение")
            log.info("support delivered to group %s for user %s", SUPPORT_GROUP_ID, message.from_user.id)
        except Exception:
            log.exception("support errors: failed to deliver support message to group %s", SUPPORT_GROUP_ID)

    await state.clear()
    if delivered_admins:
        log.info("support delivered to %s admins for user %s", delivered_admins, message.from_user.id)
        await message.answer(
            "✅ Сообщение успешно отправлено администрации.\n\nМы постараемся ответить как можно скорее.",
            reply_markup=main_menu(),
        )
    else:
        await message.answer("Не удалось отправить сообщение администрации. Попробуй позже.", reply_markup=main_menu())


@dp.callback_query(F.data.startswith("support_reply:"))
async def cb_support_reply(call: types.CallbackQuery, state: FSMContext):
    if call.from_user.id not in ADMIN_IDS:
        await call.answer("Команда не найдена.", show_alert=True)
        return
    target_user_id = int(call.data.split(":", 1)[1])
    await state.set_state(GenState.waiting_admin_reply)
    await state.update_data(support_reply_user_id=target_user_id)
    await call.message.answer("Введите ответ пользователю.")
    await call.answer()


@dp.callback_query(F.data == "support_close")
async def cb_support_close(call: types.CallbackQuery):
    if call.from_user.id not in ADMIN_IDS:
        await call.answer("Команда не найдена.", show_alert=True)
        return
    await call.answer("Закрыто")
    try:
        await call.message.edit_reply_markup(reply_markup=None)
    except TelegramBadRequest:
        pass


@dp.message(GenState.waiting_admin_reply)
async def admin_support_reply_input(message: types.Message, state: FSMContext):
    if message.from_user is None or message.from_user.id not in ADMIN_IDS:
        await state.clear()
        await message.answer("Команда не найдена.")
        return
    data = await state.get_data()
    target_user_id = int(data.get("support_reply_user_id", 0) or 0)
    text = (message.text or message.caption or "").strip()
    if not target_user_id or not text:
        await message.answer("Введите текстовый ответ пользователю или /cancel.")
        return
    try:
        await message.bot.send_message(target_user_id, "💬 Ответ администрации\n\n" + text)
        log.info("admin replied to support user %s by admin %s", target_user_id, message.from_user.id)
        await state.clear()
        await message.answer("✅ Ответ отправлен пользователю.", reply_markup=admin_panel_menu())
    except Exception:
        log.exception("support errors: failed to send admin reply to user %s", target_user_id)
        await message.answer("Не удалось отправить ответ пользователю. Попробуй позже.")


@dp.message(Command("admin", "xxx"))
async def admin_cmd(message: types.Message):
    await show_admin_panel(message)


@dp.message(Command("admin_stats"))
async def admin_stats_cmd(message: types.Message):
    if not message.from_user or message.from_user.id not in ADMIN_IDS:
        await message.answer("Команда не найдена.")
        return
    await message.answer(format_admin_stats(build_admin_stats()), parse_mode="HTML", reply_markup=admin_panel_menu())

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

@dp.message(Command("basic_defaults"))
async def basic_defaults_cmd(message: types.Message):
    if message.from_user.id not in ADMIN_IDS:
        await message.answer("Команда не найдена.")
        return
    await message.answer(basic_defaults_text(), parse_mode="HTML", reply_markup=basic_defaults_menu())


@dp.message(Command("basic_defaults_show"))
async def basic_defaults_show_cmd(message: types.Message):
    if message.from_user.id not in ADMIN_IDS:
        await message.answer("Команда не найдена.")
        return
    raw = get_config_value("basic_generation_defaults", None)
    prefix = "" if isinstance(raw, dict) else "ℹ️ Сохранённые дефолты не найдены; используются заводские.\n\n"
    await message.answer(prefix + basic_defaults_text(raw, saved=isinstance(raw, dict)), parse_mode="HTML", reply_markup=basic_defaults_menu())


@dp.message(Command("basic_defaults_reset"))
async def basic_defaults_reset_cmd(message: types.Message):
    if message.from_user.id not in ADMIN_IDS:
        await message.answer("Команда не найдена.")
        return
    delete_config_value("basic_generation_defaults")
    await message.answer("♻ Basic defaults reset. Factory defaults now apply.", reply_markup=basic_defaults_menu())


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


@dp.message(Command("set_artraccoon_vibe"))
async def set_artraccoon_vibe_cmd(message: types.Message, state: FSMContext):
    if message.from_user.id not in ADMIN_IDS:
        await message.answer("Команда не найдена.")
        return
    await state.set_state(GenState.waiting_ar_vibe)
    await message.answer("Пришли скрытый ArtRaccoon vibe prompt.")


@dp.message(Command("show_artraccoon_vibe"))
async def show_artraccoon_vibe_cmd(message: types.Message):
    if message.from_user.id not in ADMIN_IDS:
        await message.answer("Команда не найдена.")
        return
    await message.answer(f"🦝 <b>ArtRaccoon vibe</b>\n<blockquote expandable>{html.escape(artraccoon_vibe_prompt() or '—')}</blockquote>", parse_mode="HTML")


@dp.message(Command("clear_artraccoon_vibe"))
async def clear_artraccoon_vibe_cmd(message: types.Message):
    if message.from_user.id not in ADMIN_IDS:
        await message.answer("Команда не найдена.")
        return
    set_config_value("artraccoon_vibe_prompt", "")
    await message.answer("✅ ArtRaccoon vibe очищен.")


@dp.message(GenState.waiting_ar_vibe)
async def save_artraccoon_vibe(message: types.Message, state: FSMContext):
    if message.from_user.id not in ADMIN_IDS:
        await state.clear()
        await message.answer("Команда не найдена.")
        return
    set_config_value("artraccoon_vibe_prompt", (message.text or "").strip())
    await state.clear()
    await message.answer("✅ ArtRaccoon vibe сохранён.")

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
    if message.from_user.id not in ADMIN_IDS:
        await message.answer("Команда не найдена.")
        return
    s = get_settings(message.from_user.id)
    await message.answer(f"<pre>{s.to_dict()}</pre>", parse_mode="HTML")


@dp.message(Command("nai_debug"))
async def nai_debug_cmd(message: types.Message):
    if message.from_user.id not in ADMIN_IDS:
        await message.answer("Команда не найдена.")
        return
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
        await message.answer(DAILY_LIMIT_TEXT, reply_markup=main_menu())
        return

    cd = cooldown_remaining(user_id)
    if cd > 0:
        await message.answer(cooldown_text(cd), reply_markup=main_menu())
        return

    s = patch_settings(user_id, last_prompt=prompt)
    s = apply_anlas_safe_defaults(user_id)
    original_prompt = s.pending_original_prompt or prompt
    visible_prompt = prompt
    generation_prompt, hidden_vibe_applied = visible_prompt_with_optional_vibe(user_id, visible_prompt)
    try:
        final_prompt = nai.build_prompt(generation_prompt, s)
    except Exception:
        final_prompt = generation_prompt

    global generation_waiting
    if generation_lock.locked() or generation_waiting:
        await message.answer(f"⏳ Генерация поставлена в очередь. Перед тобой: {generation_waiting}")
    generation_waiting += 1
    counted = True
    wait = await message.answer(GENERATION_STARTED_TEXT)

    image_bytes = None

    async def show_character_payload_fallback() -> None:
        await wait.edit_text("NovelAI не принял Character Payload, пробую fallback-сборку.")

    try:
        if is_advanced_user(user_id) and s.pending_image_path and Path(s.pending_image_path).exists():
            image_bytes = Path(s.pending_image_path).read_bytes()
        elif is_advanced_user(user_id) and message.reply_to_message and message.reply_to_message.photo:
            photo = message.reply_to_message.photo[-1]
            file = await message.bot.get_file(photo.file_id)
            bio = BytesIO()
            await message.bot.download_file(file.file_path, destination=bio)
            image_bytes = bio.getvalue()

        async with generation_lock:
            generation_waiting = max(0, generation_waiting - 1)
            counted = False
            mark_generation_started(user_id)
            candidates = learn_from_english_prompt(visible_prompt) if looks_like_english_tags(visible_prompt) else []
            await send_moderation_copy(message.bot, user, original_prompt, final_prompt, s, candidates, hidden_vibe_applied)
            images = await asyncio.wait_for(
                nai.generate(
                    generation_prompt,
                    s,
                    image_bytes=image_bytes,
                    on_character_payload_fallback=show_character_payload_fallback,
                ),
                timeout=GENERATION_TIMEOUT_SECONDS,
            )
            set_last_payload(user_id, sanitize_payload(nai.last_payload))
        timestamp = datetime.now(timezone.utc).isoformat()
        saved_images = save_generated_images(user_id, timestamp, images)
        history_item = {
            "prompt": visible_prompt,
            "original_prompt": original_prompt,
            "final_prompt": visible_prompt,
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
        patch_settings(user_id, pending_image_path="", pending_prompt="", pending_original_prompt="", prompt_action="")
        await wait.delete()

        for idx, img in enumerate(images, start=1):
            name = f"novelai_{idx}.png"
            image = BufferedInputFile(img, filename=name)
            caption = generation_result_caption(s.model_name, s.width, s.height, s.seed)
            try:
                await send_moderation_image(message.bot, user, img, original_prompt, final_prompt, s, idx, candidates, hidden_vibe_applied)
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
    finally:
        if counted:
            generation_waiting = max(0, generation_waiting - 1)


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



def _admin_identity_line(user_id: str, raw: dict) -> str:
    username = str(raw.get("username") or "").strip()
    full_name = str(raw.get("full_name") or "").strip()
    if not full_name:
        full_name = " ".join(str(raw.get(k) or "").strip() for k in ("first_name", "last_name")).strip()
    return f"👤 id={html.escape(str(raw.get('id') or user_id))} / @{html.escape(username) if username else '-'} / {html.escape(full_name) if full_name else '-'}"


def _sorted_admin_users() -> list[tuple[str, dict]]:
    users = load_all_users_for_admin_stats()
    def sort_key(item):
        uid, raw = item
        if not isinstance(raw, dict):
            raw = {}
        seen = str(raw.get("last_seen_at") or "")
        return (seen, int(uid) if str(uid).isdigit() else 0)
    normalized = [(uid, raw if isinstance(raw, dict) else {}) for uid, raw in users.items()]
    return sorted(normalized, key=sort_key, reverse=True)


def admin_users_page_menu(page: int, total_pages: int) -> InlineKeyboardMarkup:
    nav = []
    if page > 0:
        nav.append(InlineKeyboardButton(text="◀️ Назад", callback_data=f"admin_users:all:{page - 1}"))
    if page + 1 < total_pages:
        nav.append(InlineKeyboardButton(text="▶️ Далее", callback_data=f"admin_users:all:{page + 1}"))
    keyboard = []
    if nav:
        keyboard.append(nav)
    keyboard.append([InlineKeyboardButton(text="⬅️ Назад в админку", callback_data="admin:menu")])
    return InlineKeyboardMarkup(inline_keyboard=keyboard)


def admin_users_page_text(page: int, per_page: int = 20) -> tuple[str, InlineKeyboardMarkup]:
    users = _sorted_admin_users()
    total = len(users)
    total_pages = max(1, (total + per_page - 1) // per_page)
    page = max(0, min(page, total_pages - 1))
    chunk = users[page * per_page:(page + 1) * per_page]
    lines = [_admin_identity_line(uid, raw) for uid, raw in chunk]
    text = f"👥 <b>Все пользователи</b>\nСтраница <code>{page + 1}/{total_pages}</code> · всего: <code>{total}</code>\n\n" + ("\n".join(lines) or "—")
    return text, admin_users_page_menu(page, total_pages)


async def refresh_admin_user_identities(progress_message: types.Message) -> tuple[int, int, int]:
    users = load_all_users_for_admin_stats()
    user_ids = [int(uid) for uid in users if str(uid).isdigit()]
    total = len(user_ids)
    updated = 0
    unavailable = 0
    last_edit = 0.0
    loop = asyncio.get_running_loop()
    for index, user_id in enumerate(user_ids, 1):
        try:
            chat = await progress_message.bot.get_chat(user_id)
            update_user_identity(chat)
            updated += 1
        except (TelegramBadRequest, TelegramForbiddenError):
            unavailable += 1
        except Exception:
            unavailable += 1
            log.exception("Failed to refresh Telegram identity for user_id=%s", user_id)
        now = loop.time()
        if index == total or index % 10 == 0 or now - last_edit >= 2:
            last_edit = now
            try:
                await progress_message.edit_text(
                    "🔄 <b>Обновление пользователей</b>\n\n"
                    f"Обработано: <code>{index}/{total}</code>\n"
                    f"Обновлено: <code>{updated}</code>\n"
                    f"Недоступно: <code>{unavailable}</code>",
                    parse_mode="HTML",
                    reply_markup=admin_users_menu(),
                )
            except (TelegramBadRequest, TelegramForbiddenError):
                pass
        await asyncio.sleep(0.1)
    return total, updated, unavailable


def _admin_user_summary(user_id: int) -> str:
    raw = get_user_record_for_admin(user_id)
    if not raw:
        return f"👤 Пользователь <code>{user_id}</code> не найден."
    history = raw.get("history", []) if isinstance(raw.get("history"), list) else []
    favorites = raw.get("favorites", []) if isinstance(raw.get("favorites"), list) else []
    username = str(raw.get("username") or "").strip() or "-"
    full_name = str(raw.get("full_name") or "").strip()
    if not full_name:
        full_name = " ".join(str(raw.get(k) or "").strip() for k in ("first_name", "last_name")).strip() or "-"
    daily = int(raw.get("free_daily_used", raw.get("daily_generation_count", 0)) or 0)
    return (
        f"👤 <b>Пользователь</b>\n"
        f"🆔 ID: <code>{user_id}</code>\n"
        f"🔗 Username: <code>@{html.escape(username) if username != '-' else '-'}</code>\n"
        f"👤 Full name: <code>{html.escape(full_name)}</code>\n"
        f"💎 Balance: <code>{int(raw.get('paid_generations_balance', 0) or 0)}</code>\n"
        f"🖼 Total generations: <code>{len(history)}</code>\n"
        f"📅 Daily usage: <code>{daily}</code>\n"
        f"📚 History count: <code>{len(history)}</code>\n"
        f"⭐ Favorites count: <code>{len(favorites)}</code>\n"
        f"🦝 ArtRaccoon vibe enabled: <code>{bool(raw.get('artraccoon_vibe_enabled'))}</code>\n"
        f"🕘 Last seen: <code>{html.escape(str(raw.get('last_seen_at') or '—'))}</code>"
    )


def _admin_items_text(user_id: int, key: str, title: str) -> str:
    raw = get_user_record_for_admin(user_id)
    items = raw.get(key, []) if isinstance(raw, dict) else []
    if not isinstance(items, list) or not items:
        return f"{title} <code>{user_id}</code>\n\n—"
    lines = []
    for i, item in enumerate(items[:10], 1):
        prompt = item.get("prompt") if isinstance(item, dict) else str(item)
        ts = item.get("timestamp", "—") if isinstance(item, dict) else "—"
        lines.append(f"{i}. <code>{html.escape(str(ts))}</code>\n{html.escape(str(prompt or '—')[:350])}")
    return f"{title} <code>{user_id}</code>\n\n" + "\n\n".join(lines)


def _users_count() -> int:
    return len(load_all_users_for_admin_stats())


def _normalized_extra_characters(settings: UserSettings) -> list[dict]:
    chars = settings.extra_characters if isinstance(settings.extra_characters, list) else []
    normalized = []
    for item in chars[:MAX_EXTRA_CHARACTERS]:
        if not isinstance(item, dict):
            continue
        normalized.append({
            "prompt": str(item.get("prompt") or "").strip(),
            "uc": str(item.get("uc") or "").strip(),
            "position": str(item.get("position") or "").strip(),
        })
    return normalized


def _characters_text(characters: list[dict]) -> str:
    lines = ["👥 <b>Character+</b>", "Admin-only multi-character captions for NovelAI V4/V4.5."]
    if not characters:
        lines.append("\nПока дополнительных персонажей нет.")
    else:
        lines.append(f"\nПерсонажей: <code>{len(characters)}/{MAX_EXTRA_CHARACTERS}</code>")
        for idx, ch in enumerate(characters, start=1):
            lines.extend([
                f"\n<b>{idx}.</b>",
                f"Prompt: <blockquote expandable>{html.escape(ch.get('prompt') or '—')}</blockquote>",
                f"UC: <blockquote expandable>{html.escape(ch.get('uc') or '—')}</blockquote>",
                f"Position: <code>{html.escape(ch.get('position') or '—')}</code>",
            ])
    return "\n".join(lines)


async def show_characters_panel(message: types.Message, user_id: int, *, edit: bool = False) -> None:
    chars = _normalized_extra_characters(get_settings(user_id))
    text = _characters_text(chars)
    markup = characters_menu(chars)
    if edit:
        await message.edit_text(text, parse_mode="HTML", reply_markup=markup)
    else:
        await message.answer(text, parse_mode="HTML", reply_markup=markup)


@dp.message(Command("characters", "char"))
async def characters_cmd(message: types.Message):
    if message.from_user.id not in ADMIN_IDS:
        await message.answer("Команда не найдена.")
        return
    await show_characters_panel(message, message.from_user.id)


@dp.callback_query(F.data.startswith("char:"))
async def cb_characters(call: types.CallbackQuery, state: FSMContext):
    if call.from_user.id not in ADMIN_IDS:
        await call.answer("Команда не найдена.", show_alert=True)
        return
    action = call.data.split(":", 2)[1]
    settings = get_settings(call.from_user.id)
    chars = _normalized_extra_characters(settings)
    if action == "menu":
        await show_characters_panel(call.message, call.from_user.id, edit=True)
    elif action == "add":
        if len(chars) >= MAX_EXTRA_CHARACTERS:
            await call.answer("Достигнут лимит Character+", show_alert=True)
            return
        await state.set_state(GenState.waiting_char_prompt)
        await call.message.answer("Пришли prompt дополнительного персонажа.", reply_markup=characters_menu(chars))
    elif action == "show":
        await call.message.answer(_characters_text(chars), parse_mode="HTML", reply_markup=characters_menu(chars))
    elif action == "delete":
        idx = int(call.data.split(":", 2)[2])
        if 0 <= idx < len(chars):
            chars.pop(idx)
            patch_settings(call.from_user.id, extra_characters=chars)
            await call.message.edit_text(_characters_text(chars), parse_mode="HTML", reply_markup=characters_menu(chars))
        else:
            await call.answer("Персонаж не найден", show_alert=True)
    elif action == "clear":
        patch_settings(call.from_user.id, extra_characters=[])
        await call.message.edit_text(_characters_text([]), parse_mode="HTML", reply_markup=characters_menu([]))
    await call.answer()


@dp.message(GenState.waiting_char_prompt)
async def char_prompt_input(message: types.Message, state: FSMContext):
    if message.from_user.id not in ADMIN_IDS:
        await message.answer("Команда не найдена.")
        await state.clear()
        return
    await state.update_data(char_prompt=(message.text or "").strip())
    await state.set_state(GenState.waiting_char_uc)
    await message.answer("Пришли UC/negative для персонажа (или '-' чтобы оставить пустым).")


@dp.message(GenState.waiting_char_uc)
async def char_uc_input(message: types.Message, state: FSMContext):
    if message.from_user.id not in ADMIN_IDS:
        await message.answer("Команда не найдена.")
        await state.clear()
        return
    raw = (message.text or "").strip()
    await state.update_data(char_uc="" if raw == "-" else raw)
    await state.set_state(GenState.waiting_char_position)
    await message.answer("Пришли position (например left/center/right) или '-' чтобы оставить пустым.")


@dp.message(GenState.waiting_char_position)
async def char_position_input(message: types.Message, state: FSMContext):
    if message.from_user.id not in ADMIN_IDS:
        await message.answer("Команда не найдена.")
        await state.clear()
        return
    data = await state.get_data()
    position = (message.text or "").strip()
    item = {"prompt": data.get("char_prompt", ""), "uc": data.get("char_uc", ""), "position": "" if position == "-" else position}
    chars = _normalized_extra_characters(get_settings(message.from_user.id))
    if item["prompt"] and len(chars) < MAX_EXTRA_CHARACTERS:
        chars.append(item)
        patch_settings(message.from_user.id, extra_characters=chars)
    await state.clear()
    await message.answer("✅ Character+ персонаж сохранён.", reply_markup=characters_menu(chars))

@dp.callback_query(F.data.startswith("admin:"))
async def cb_admin_panel(call: types.CallbackQuery):
    if call.from_user.id not in ADMIN_IDS:
        await call.answer("Команда не найдена.", show_alert=True)
        return
    action = call.data.split(":", 1)[1]
    if action == "menu":
        await call.message.edit_text(admin_panel_text(), parse_mode="HTML", reply_markup=admin_panel_menu())
    elif action == "stats":
        await call.message.edit_text(format_admin_stats(build_admin_stats()), parse_mode="HTML", reply_markup=admin_panel_menu())
    elif action == "basic_defaults":
        await call.message.edit_text(basic_defaults_text(), parse_mode="HTML", reply_markup=basic_defaults_menu())
    elif action == "ar_vibe":
        await call.message.edit_text("🦝 <b>ArtRaccoon Vibe</b>", parse_mode="HTML", reply_markup=admin_ar_vibe_menu())
    elif action == "nai_debug":
        await call.message.edit_text("🧪 <b>NovelAI debug</b>", parse_mode="HTML", reply_markup=admin_nai_debug_menu())
    elif action == "characters":
        await show_characters_panel(call.message, call.from_user.id, edit=True)
    elif action == "dict":
        await call.message.edit_text("📚 <b>Dictionary</b>", parse_mode="HTML", reply_markup=dictionary_menu())
    elif action == "purchases":
        await call.message.edit_text("💎 <b>Покупки / генерации</b>", parse_mode="HTML", reply_markup=admin_purchases_menu())
    elif action == "users":
        await call.message.edit_text("👥 <b>Пользователи</b>", parse_mode="HTML", reply_markup=admin_users_menu())
    elif action == "broadcast":
        draft = get_config_value(f"broadcast_draft:{call.from_user.id}", "")
        await call.message.edit_text(f"📢 <b>Рассылка</b>\n\nЧерновик: <code>{'есть' if draft else 'нет'}</code>", parse_mode="HTML", reply_markup=admin_broadcast_menu(bool(draft)))
    else:
        await call.message.edit_text("Раздел скоро добавим.", reply_markup=admin_panel_menu())
    await call.answer()


@dp.callback_query(F.data.startswith("admin_ar_vibe:"))
async def cb_admin_ar_vibe(call: types.CallbackQuery, state: FSMContext):
    if call.from_user.id not in ADMIN_IDS:
        await call.answer("Команда не найдена.", show_alert=True)
        return
    action = call.data.split(":", 1)[1]
    if action == "show":
        await call.message.edit_text(f"🦝 <b>ArtRaccoon vibe</b>\n<blockquote expandable>{html.escape(artraccoon_vibe_prompt() or '—')}</blockquote>", parse_mode="HTML", reply_markup=admin_ar_vibe_menu())
    elif action == "set":
        await state.set_state(GenState.waiting_ar_vibe)
        await call.message.answer("Пришли скрытый ArtRaccoon vibe prompt.", reply_markup=admin_ar_vibe_menu())
    elif action == "clear":
        set_config_value("artraccoon_vibe_prompt", "")
        await call.message.edit_text("✅ ArtRaccoon vibe очищен.", reply_markup=admin_ar_vibe_menu())
    await call.answer()


@dp.callback_query(F.data.startswith("admin_nai:"))
async def cb_admin_nai(call: types.CallbackQuery):
    if call.from_user.id not in ADMIN_IDS:
        await call.answer("Команда не найдена.", show_alert=True)
        return
    action = call.data.split(":", 1)[1]
    s = get_settings(call.from_user.id)
    if action == "summary":
        payload = get_last_payload(call.from_user.id) or sanitize_payload(nai.build_payload(s.last_prompt or "debug prompt", s))
        await call.message.answer(nai_payload_summary_text(payload, s), parse_mode="HTML", reply_markup=admin_nai_debug_menu())
    elif action == "full":
        payload = sanitize_payload(get_last_payload(call.from_user.id) or nai.build_payload(s.last_prompt or "debug prompt", s))
        data = json.dumps(payload, ensure_ascii=False, indent=2).encode("utf-8")
        await call.message.answer_document(BufferedInputFile(data, filename="novelai_payload.json"), reply_markup=admin_nai_debug_menu())
    elif action == "compare":
        meta = get_last_metadata(call.from_user.id)
        payload = sanitize_payload(get_last_payload(call.from_user.id))
        text = "📭 Last NovelAI metadata не сохранена. Ответь /meta на файл с metadata NovelAI." if not meta else ("📭 Last bot payload не сохранён. Сгенерируй изображение или используй /nai_payload для preview." if not payload else nai_compare_summary_text(meta, payload))
        await call.message.answer(text, parse_mode="HTML", reply_markup=admin_nai_debug_menu())
    elif action == "site_clone":
        await call.message.answer("🌐 <b>Admin/Site Clone mode</b>", parse_mode="HTML", reply_markup=admin_site_clone_menu())
    elif action == "site_fields":
        await call.message.answer("🌐 <b>Supported NovelAI website fields</b>\n<pre>" + html.escape(registry_fields_text(admin=True)) + "</pre>", parse_mode="HTML", reply_markup=admin_site_clone_menu())
    elif action == "apply_meta_settings":
        meta = get_last_metadata(call.from_user.id)
        if not meta:
            await call.answer("Metadata не найдена", show_alert=True)
            return
        updates = metadata_settings_updates(meta, admin=True)
        if not updates:
            await call.answer("В metadata нет известных website settings", show_alert=True)
            return
        patch_settings(call.from_user.id, **updates)
        await call.message.answer("✅ Website settings применены из metadata.", reply_markup=admin_site_clone_menu())
    await call.answer()

@dp.callback_query(F.data.startswith("basic_defaults:"))
async def cb_basic_defaults(call: types.CallbackQuery, state: FSMContext):
    if call.from_user.id not in ADMIN_IDS:
        await call.answer("Команда не найдена.", show_alert=True)
        return
    parts = call.data.split(":")
    action = parts[1] if len(parts) > 1 else ""
    if action == "save":
        defaults = basic_defaults_from_settings(get_settings(call.from_user.id))
        defaults = sanitize_basic_defaults(defaults, clamp_steps=True)
        defaults["steps"] = min(28, int(defaults["steps"]))
        defaults["scale"] = min(10.0, max(1.0, float(defaults["scale"])))
        defaults["n_samples"] = 1
        defaults["seed"] = -1
        set_config_value("basic_generation_defaults", defaults)
        await call.message.edit_text("✅ Настройки сохранены.\n\n" + basic_defaults_text(defaults, saved=True), parse_mode="HTML", reply_markup=basic_defaults_menu())
        await call.answer("Сохранено")
        return
    if action == "choose_model":
        await call.message.edit_text("🧠 Выбери модель для обычного режима.", reply_markup=basic_defaults_select_menu("model"))
        await call.answer()
        return
    if action == "choose_size":
        await call.message.edit_text("📐 Выбери безопасный размер для обычного режима.", reply_markup=basic_defaults_select_menu("size"))
        await call.answer()
        return
    if action == "choose_sampler":
        await call.message.edit_text("🧪 Выбери sampler для обычного режима.", reply_markup=basic_defaults_select_menu("sampler"))
        await call.answer()
        return
    if action == "set_model" and len(parts) >= 3:
        name = call.data.split(":", 2)[2]
        if name not in MODELS:
            await call.answer("Неизвестная модель", show_alert=True)
            return
        defaults = update_basic_defaults_config(model_name=name)
        await call.message.edit_text(basic_defaults_text(defaults, saved=True), parse_mode="HTML", reply_markup=basic_defaults_menu())
        await call.answer("Модель обновлена")
        return
    if action == "set_size" and len(parts) >= 3:
        name = call.data.split(":", 2)[2]
        size = RESOLUTIONS.get(name)
        if size not in SAFE_RESOLUTIONS:
            await call.answer("Этот размер недоступен в обычном режиме", show_alert=True)
            return
        defaults = update_basic_defaults_config(width=size[0], height=size[1])
        await call.message.edit_text(basic_defaults_text(defaults, saved=True), parse_mode="HTML", reply_markup=basic_defaults_menu())
        await call.answer("Размер обновлён")
        return
    if action == "set_sampler" and len(parts) >= 3:
        sampler = call.data.split(":", 2)[2]
        if sampler not in SAMPLERS:
            await call.answer("Неизвестный sampler", show_alert=True)
            return
        defaults = update_basic_defaults_config(sampler=sampler)
        await call.message.edit_text(basic_defaults_text(defaults, saved=True), parse_mode="HTML", reply_markup=basic_defaults_menu())
        await call.answer("Sampler обновлён")
        return
    if action == "ask_steps":
        await state.set_state(GenState.waiting_basic_steps)
        await call.message.answer("👣 Пришли количество шагов для обычного режима (1–28).")
        await call.answer()
        return
    if action == "ask_cfg":
        await state.set_state(GenState.waiting_basic_cfg)
        await call.message.answer("🧲 Пришли CFG для обычного режима (1.0–10.0).")
        await call.answer()
        return
    if action == "ask_negative":
        await state.set_state(GenState.waiting_basic_negative)
        await call.message.answer("🧯 Пришли негатив для обычного режима. Пусто или <code>-</code> — очистить.", parse_mode="HTML")
        await call.answer()
        return
    if action == "toggle_quality":
        current = saved_basic_defaults()
        defaults = update_basic_defaults_config(add_quality_tags=not bool(current.get("add_quality_tags")))
        await call.message.edit_text(basic_defaults_text(defaults, saved=True), parse_mode="HTML", reply_markup=basic_defaults_menu())
        await call.answer("Quality tags переключены")
        return
    if action == "toggle_variety":
        current = saved_basic_defaults()
        defaults = update_basic_defaults_config(variety_plus=not bool(current.get("variety_plus")))
        await call.message.edit_text(basic_defaults_text(defaults, saved=True), parse_mode="HTML", reply_markup=basic_defaults_menu())
        await call.answer("Variety+ переключён")
        return
    if action == "show":
        raw = get_config_value("basic_generation_defaults", None)
        prefix = "" if isinstance(raw, dict) else "ℹ️ Сохранённые дефолты не найдены; используются заводские.\n\n"
        await call.message.edit_text(prefix + basic_defaults_details_text(raw, saved=isinstance(raw, dict)), parse_mode="HTML", reply_markup=basic_defaults_menu())
        await call.answer()
        return
    if action == "reset":
        delete_config_value("basic_generation_defaults")
        await call.message.edit_text("♻️ Дефолты сброшены.\n\n" + basic_defaults_text(factory_basic_defaults(), saved=False), parse_mode="HTML", reply_markup=basic_defaults_menu())
        await call.answer("Сброшено")
        return
    if action == "test":
        test_settings = settings_from_basic_defaults()
        payload = sanitize_payload(nai.build_payload("test prompt", test_settings))
        await call.message.edit_text("🧪 <b>Тестовый payload дефолтов</b>\nPrompt: <code>test prompt</code>\n\n" + nai_payload_summary_text(payload, test_settings), parse_mode="HTML", reply_markup=basic_defaults_menu())
        await call.answer()
        return
    await call.answer("Unknown action", show_alert=True)



@dp.message(GenState.waiting_basic_steps)
async def basic_steps_input(message: types.Message, state: FSMContext):
    if message.from_user.id not in ADMIN_IDS:
        await state.clear()
        await message.answer("Команда не найдена.")
        return
    try:
        steps = parse_basic_steps(message.text or "")
    except ValueError:
        await message.answer("Нужно число от 1 до 28.", reply_markup=basic_defaults_menu())
        return
    await state.clear()
    defaults = update_basic_defaults_config(steps=steps)
    await message.answer("👣 Шаги обновлены.\n\n" + basic_defaults_text(defaults, saved=True), parse_mode="HTML", reply_markup=basic_defaults_menu())


@dp.message(GenState.waiting_basic_cfg)
async def basic_cfg_input(message: types.Message, state: FSMContext):
    if message.from_user.id not in ADMIN_IDS:
        await state.clear()
        await message.answer("Команда не найдена.")
        return
    try:
        cfg = parse_basic_cfg(message.text or "")
    except ValueError:
        await message.answer("Нужно число от 1.0 до 10.0.", reply_markup=basic_defaults_menu())
        return
    await state.clear()
    defaults = update_basic_defaults_config(scale=cfg)
    await message.answer("🧲 CFG обновлён.\n\n" + basic_defaults_text(defaults, saved=True), parse_mode="HTML", reply_markup=basic_defaults_menu())


@dp.message(GenState.waiting_basic_negative)
async def basic_negative_input(message: types.Message, state: FSMContext):
    if message.from_user.id not in ADMIN_IDS:
        await state.clear()
        await message.answer("Команда не найдена.")
        return
    text = (message.text or "").strip()
    defaults = update_basic_defaults_config(negative_prompt="" if text in {"", "-"} else text)
    await state.clear()
    await message.answer("🧯 Негатив обновлён.\n\n" + basic_defaults_text(defaults, saved=True), parse_mode="HTML", reply_markup=basic_defaults_menu())


@dp.callback_query(F.data.startswith("admin_purchases:"))
async def cb_admin_purchases(call: types.CallbackQuery, state: FSMContext):
    if call.from_user.id not in ADMIN_IDS:
        await call.answer("Команда не найдена.", show_alert=True)
        return
    action = call.data.split(":", 1)[1]
    await state.update_data(admin_purchase_action=action)
    await state.set_state(GenState.waiting_purchase_user_id)
    await call.message.answer("Пришли user_id.", reply_markup=admin_purchases_menu())
    await call.answer()


@dp.message(GenState.waiting_purchase_user_id)
async def admin_purchase_user_id(message: types.Message, state: FSMContext):
    if message.from_user.id not in ADMIN_IDS:
        await state.clear()
        await message.answer("Команда не найдена.")
        return
    try:
        user_id = int((message.text or "").strip())
    except ValueError:
        await message.answer("Нужен числовой user_id.", reply_markup=admin_purchases_menu())
        return
    data = await state.get_data()
    action = data.get("admin_purchase_action")
    await state.update_data(admin_purchase_user_id=user_id)
    if action in {"find", "balance"}:
        await state.clear()
        await message.answer(_admin_user_summary(user_id), parse_mode="HTML", reply_markup=admin_purchases_menu())
        return
    await state.set_state(GenState.waiting_purchase_amount)
    await message.answer("Пришли amount числом.", reply_markup=admin_purchases_menu())


@dp.message(GenState.waiting_purchase_amount)
async def admin_purchase_amount(message: types.Message, state: FSMContext):
    if message.from_user.id not in ADMIN_IDS:
        await state.clear()
        await message.answer("Команда не найдена.")
        return
    try:
        amount = abs(int((message.text or "").strip()))
    except ValueError:
        await message.answer("Нужен amount числом.", reply_markup=admin_purchases_menu())
        return
    data = await state.get_data()
    user_id = int(data.get("admin_purchase_user_id"))
    delta = amount if data.get("admin_purchase_action") == "add" else -amount
    balance = adjust_paid_generations_balance(user_id, delta)
    await state.clear()
    await message.answer(f"✅ Баланс пользователя <code>{user_id}</code>: <code>{balance}</code>", parse_mode="HTML", reply_markup=admin_purchases_menu())


@dp.callback_query(F.data.startswith("admin_users:"))
async def cb_admin_users(call: types.CallbackQuery, state: FSMContext):
    if call.from_user.id not in ADMIN_IDS:
        await call.answer("Команда не найдена.", show_alert=True)
        return
    action = call.data.split(":", 1)[1]
    if action.startswith("all"):
        parts = call.data.split(":")
        page = int(parts[2]) if len(parts) > 2 and parts[2].isdigit() else 0
        text, markup = admin_users_page_text(page)
        await call.message.edit_text(text, parse_mode="HTML", reply_markup=markup)
    elif action == "refresh":
        await call.answer("Запускаю обновление")
        progress = await call.message.answer("🔄 <b>Обновление пользователей</b>\n\nОбработано: <code>0</code>", parse_mode="HTML", reply_markup=admin_users_menu())
        total, updated, unavailable = await refresh_admin_user_identities(progress)
        await progress.edit_text(
            "✅ Обновление завершено\n\n"
            f"Всего: <code>{total}</code>\n"
            f"Обновлено: <code>{updated}</code>\n"
            f"Недоступно: <code>{unavailable}</code>",
            parse_mode="HTML",
            reply_markup=admin_users_menu(),
        )
        return
    else:
        await state.update_data(admin_user_action=action)
        await state.set_state(GenState.waiting_admin_user_id)
        await call.message.answer("Пришли user_id.", reply_markup=admin_users_menu())
    await call.answer()


@dp.message(GenState.waiting_admin_user_id)
async def admin_user_id_answer(message: types.Message, state: FSMContext):
    if message.from_user.id not in ADMIN_IDS:
        await state.clear()
        await message.answer("Команда не найдена.")
        return
    try:
        user_id = int((message.text or "").strip())
    except ValueError:
        await message.answer("Нужен числовой user_id.", reply_markup=admin_users_menu())
        return
    action = (await state.get_data()).get("admin_user_action")
    await state.clear()
    if action == "history":
        text = _admin_items_text(user_id, "history", "🖼 <b>История пользователя</b>")
    elif action == "favorites":
        text = _admin_items_text(user_id, "favorites", "⭐ <b>Избранное пользователя</b>")
    elif action == "clear_draft":
        ok = clear_user_draft_for_admin(user_id)
        text = "✅ Черновик очищен." if ok else "👤 Пользователь не найден."
    else:
        text = _admin_user_summary(user_id)
    await message.answer(text, parse_mode="HTML", reply_markup=admin_users_menu())


@dp.callback_query(F.data.startswith("admin_broadcast:"))
async def cb_admin_broadcast(call: types.CallbackQuery, state: FSMContext):
    if call.from_user.id not in ADMIN_IDS:
        await call.answer("Команда не найдена.", show_alert=True)
        return
    action = call.data.split(":", 1)[1]
    key = f"broadcast_draft:{call.from_user.id}"
    draft = str(get_config_value(key, "") or "")
    if action == "write":
        await state.set_state(GenState.waiting_broadcast_text)
        await call.message.answer("Пришли текст рассылки.", reply_markup=admin_broadcast_menu(bool(draft)))
    elif action == "test":
        if not draft:
            await call.answer("Сначала напишите сообщение", show_alert=True)
        else:
            await call.message.bot.send_message(call.from_user.id, draft, reply_markup=admin_broadcast_menu(True))
    elif action == "send_all":
        if not draft:
            await call.answer("Сначала напишите сообщение", show_alert=True)
        else:
            await call.message.answer(f"Отправить рассылку всем пользователям?\nОценка: <code>{_users_count()}</code>", parse_mode="HTML", reply_markup=admin_broadcast_confirm_menu())
    elif action == "confirm_all":
        if not draft:
            await call.answer("Черновик пуст", show_alert=True)
            return
        sent = failed = 0
        for uid in load_all_users_for_admin_stats().keys():
            try:
                await call.message.bot.send_message(int(uid), draft)
                sent += 1
            except Exception:
                failed += 1
                log.exception("Broadcast failed for user %s", uid)
        await call.message.answer(f"📢 Рассылка завершена.\n✅ Sent: <code>{sent}</code>\n❌ Failed: <code>{failed}</code>", parse_mode="HTML", reply_markup=admin_broadcast_menu(True))
    elif action == "cancel":
        delete_config_value(key)
        await state.clear()
        await call.message.answer("❌ Рассылка отменена.", reply_markup=admin_broadcast_menu(False))
    await call.answer()


@dp.message(GenState.waiting_broadcast_text)
async def admin_broadcast_text(message: types.Message, state: FSMContext):
    if message.from_user.id not in ADMIN_IDS:
        await state.clear()
        await message.answer("Команда не найдена.")
        return
    set_config_value(f"broadcast_draft:{message.from_user.id}", message.text or "")
    await state.clear()
    await message.answer(f"✅ Черновик сохранён.\nОценка получателей: <code>{_users_count()}</code>", parse_mode="HTML", reply_markup=admin_broadcast_menu(True))


@dp.callback_query(F.data == "menu:main")
async def cb_main(call: types.CallbackQuery):
    await call.message.edit_text(
        main_menu_text(),
        reply_markup=main_menu(),
        parse_mode="HTML",
    )
    await call.answer()

@dp.callback_query(F.data == "menu:settings")
async def cb_settings(call: types.CallbackQuery):
    if not is_advanced_user(call.from_user.id):
        await call.message.edit_text(PAID_PLACEHOLDER, reply_markup=main_menu())
        await call.answer()
        return
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
        prompt_request_text(),
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


@dp.callback_query(F.data == "quick:edit_prompt")
async def cb_edit_last_prompt(call: types.CallbackQuery):
    s = get_settings(call.from_user.id)
    if not s.last_prompt:
        await call.answer("Пока нечего изменить", show_alert=True)
        return
    patch_settings(call.from_user.id, pending_prompt=s.last_prompt, pending_original_prompt=s.last_prompt, prompt_action="replace")
    await call.message.answer(EDIT_PROMPT_TEXT)
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
        await call.message.answer(PROMPT_EMPTY_TEXT, reply_markup=main_menu())
        return
    patch_settings(call.from_user.id, prompt_action="")
    await call.answer("Запускаю генерацию")
    await generate_image_from_prompt(call.message, prompt, actor=call.from_user)

@dp.callback_query(F.data == "prompt:append")
async def cb_prompt_append(call: types.CallbackQuery):
    if not get_settings(call.from_user.id).pending_prompt.strip():
        await call.answer("Сначала пришли промт", show_alert=True)
        return
    patch_settings(call.from_user.id, prompt_action="append")
    await call.message.answer("✏️ Пришли, что добавить — аккуратно допишу в черновик.")
    await call.answer()

@dp.callback_query(F.data == "prompt:replace")
async def cb_prompt_replace(call: types.CallbackQuery):
    patch_settings(call.from_user.id, prompt_action="replace")
    await call.message.answer(EDIT_PROMPT_TEXT)
    await call.answer()


@dp.callback_query(F.data == "prompt:clear")
async def cb_prompt_clear(call: types.CallbackQuery):
    patch_settings(call.from_user.id, pending_prompt="", pending_original_prompt="", prompt_action="")
    await call.message.answer(CLEAR_TEXT, reply_markup=main_menu())
    await call.answer("Очищено")


@dp.callback_query(F.data == "prompt:ar_vibe")
async def cb_prompt_ar_vibe(call: types.CallbackQuery):
    vibe = artraccoon_vibe_prompt()
    if not vibe:
        patch_settings(call.from_user.id, artraccoon_vibe_enabled=False)
        await call.answer("ArtRaccoon vibe ещё не задан.", show_alert=True)
        return
    s = get_settings(call.from_user.id)
    s = patch_settings(call.from_user.id, artraccoon_vibe_enabled=not s.artraccoon_vibe_enabled)
    await call.message.edit_reply_markup(reply_markup=prompt_menu_for(s, call.from_user.id))
    await call.answer(f"ArtRaccoon vibe: {'ON' if s.artraccoon_vibe_enabled else 'OFF'}")


@dp.callback_query(F.data.startswith("paid:"))
async def cb_paid_placeholder(call: types.CallbackQuery):
    await call.message.answer(PAID_PLACEHOLDER, reply_markup=main_menu())
    await call.answer()

@dp.callback_query(F.data == "prompt:cancel")
async def cb_prompt_cancel(call: types.CallbackQuery):
    patch_settings(call.from_user.id, pending_prompt="", pending_original_prompt="", prompt_action="")
    await call.message.answer(CANCEL_TEXT, reply_markup=main_menu())
    await call.answer("Отменено")

@dp.callback_query(F.data.startswith("settings:"))
async def cb_setting_text_input(call: types.CallbackQuery, state: FSMContext):
    field = call.data.split(":", 1)[1]
    s = get_settings(call.from_user.id)
    advanced = (s.pro_mode and call.from_user.id in ADMIN_IDS) or s.artraccoon_mode
    if not is_advanced_user(call.from_user.id):
        patch_settings(call.from_user.id, pro_mode=False, n_samples=1)
        await call.answer(PAID_PLACEHOLDER, show_alert=True)
        await call.message.answer(PAID_PLACEHOLDER, reply_markup=main_menu())
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
            path = safe_existing_generated_path(str(image.get("path") or ""))
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
        return prompt
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
    if tool == "translate":
        await call.answer("Переводчик временно отключён.", show_alert=True)
        return
    prompt = transform_prompt(s.pending_prompt, tool)
    original = s.pending_original_prompt
    updates = {"pending_prompt": prompt, "pending_original_prompt": original}
    if s.artraccoon_mode:
        updates["artraccoon_character_prompt"] = original
    s = patch_settings(call.from_user.id, **updates)
    preview = art_prompt_preview_text(s) if s.artraccoon_mode else prompt_preview_text(prompt, original, s)
    await call.message.edit_text(preview, parse_mode="HTML", reply_markup=prompt_menu_for(s, call.from_user.id))
    await call.answer("Промт обновлён")

def metadata_settings_updates(meta: dict, *, admin: bool = False) -> dict:
    updates = settings_updates_from_metadata(meta, include_admin=admin)
    if not admin:
        action = str(updates.get("nai_action") or "").strip()
        if action and action != "generate":
            updates.pop("nai_action", None)
        updates.pop("upscale_action", None)
        updates.pop("variation_action", None)
        updates.pop("infill_mask", None)
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
        updates.update(metadata_settings_updates(meta, admin=call.from_user.id in ADMIN_IDS))
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
    await message.answer(CANCEL_TEXT, reply_markup=main_menu())



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
        await message.answer("🖼️ Пришли текстовый промпт или нажми /cancel.", reply_markup=main_menu())
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
        await message.answer("Команда не найдена.")
        return
    await message.answer("📚 <b>Dictionary</b>", parse_mode="HTML", reply_markup=dictionary_menu())


@dp.callback_query(F.data == "dict:menu")
async def cb_dict_menu(call: types.CallbackQuery):
    if not _is_admin(call.from_user.id):
        await call.answer("Команда не найдена.", show_alert=True)
        return
    await call.message.answer("📚 <b>Dictionary</b>", parse_mode="HTML", reply_markup=dictionary_menu())
    await call.answer()


@dp.callback_query(F.data == "dict:stats")
async def cb_dict_stats(call: types.CallbackQuery):
    if not _is_admin(call.from_user.id):
        await call.answer("Команда не найдена.", show_alert=True)
        return
    await call.message.answer(_dictionary_stats_text(), parse_mode="HTML", reply_markup=dictionary_menu())
    await call.answer()


@dp.callback_query(F.data == "dict:pending")
async def cb_dict_pending(call: types.CallbackQuery):
    if not _is_admin(call.from_user.id):
        await call.answer("Команда не найдена.", show_alert=True)
        return
    tags = load_learned_dictionary()["pending_suggestions"]
    text = "🕓 <b>Pending candidates</b>\n" + ("\n".join(f"• {html.escape(t)}" for t in tags[:40]) or "—")
    await call.message.answer(text, parse_mode="HTML", reply_markup=dictionary_pending_menu(tags))
    await call.answer()


@dp.callback_query(F.data.startswith("dict_review:"))
async def cb_dict_review(call: types.CallbackQuery, state: FSMContext):
    if not _is_admin(call.from_user.id):
        await call.answer("Команда не найдена.", show_alert=True)
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
        await call.answer("Команда не найдена.", show_alert=True)
        return
    tags = moderation_candidates.pop(call.data.split(":", 1)[1], [])
    reject_tags(tags)
    await call.message.answer("❌ Кандидаты отклонены.", reply_markup=dictionary_menu())
    await call.answer()


@dp.callback_query(F.data == "dict:reject_pending")
async def cb_dict_reject_pending(call: types.CallbackQuery):
    if not _is_admin(call.from_user.id):
        await call.answer("Команда не найдена.", show_alert=True)
        return
    tags = load_learned_dictionary()["pending_suggestions"]
    reject_tags(tags)
    await call.message.answer("❌ Все pending-кандидаты отклонены.", reply_markup=dictionary_menu())
    await call.answer()


@dp.callback_query(F.data.startswith("dict_one:"))
async def cb_dict_one(call: types.CallbackQuery, state: FSMContext):
    if not _is_admin(call.from_user.id):
        await call.answer("Команда не найдена.", show_alert=True)
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
        await call.answer("Команда не найдена.", show_alert=True)
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
        await call.answer("Команда не найдена.", show_alert=True)
        return
    await call.message.answer_document(FSInputFile(DICTIONARY_PATH), caption="learned_dictionary.json")
    await call.answer()


@dp.callback_query(F.data == "dict:import")
async def cb_dict_import_hint(call: types.CallbackQuery):
    if not _is_admin(call.from_user.id):
        await call.answer("Команда не найдена.", show_alert=True)
        return
    await call.message.answer("Пришлите JSON файлом и ответьте на него командой /dict_import.")
    await call.answer()


@dp.message(Command("dict_import"))
async def dict_import_cmd(message: types.Message):
    if not message.from_user or not _is_admin(message.from_user.id):
        await message.answer("Команда не найдена.")
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
        await call.answer("Команда не найдена.", show_alert=True)
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
        await message.answer(PROMPT_EMPTY_TEXT, reply_markup=main_menu())
        return

    s = get_settings(message.from_user.id)
    if s.prompt_action == "append" and s.pending_prompt.strip():
        original = f"{(s.pending_original_prompt or s.pending_prompt).strip()}, {text}"
        prompt, stored_original = prepare_prompt_for_user(message.from_user.id, original)
        updates = {"pending_prompt": prompt, "pending_original_prompt": stored_original, "prompt_action": ""}
        if s.artraccoon_mode:
            updates["artraccoon_character_prompt"] = stored_original
        patch_settings(message.from_user.id, **updates)
        await message.answer("✨ Добавила к черновику.")
    else:
        converted, original = prepare_prompt_for_user(message.from_user.id, text)
        updates = {"pending_prompt": converted, "pending_original_prompt": original, "prompt_action": ""}
        if s.artraccoon_mode:
            updates["artraccoon_character_prompt"] = original
        patch_settings(message.from_user.id, **updates)
        if s.prompt_action == "replace":
            await message.answer("✨ Черновик обновлён.")

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
