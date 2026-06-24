import asyncio
import logging
import os
import html
from io import BytesIO
from pathlib import Path
from datetime import datetime, timezone

from aiogram import Bot, Dispatcher, F, types
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import BufferedInputFile
from aiogram.client.session.aiohttp import AiohttpSession
from dotenv import load_dotenv

from config_defaults import QUICK_PRESETS, RESOLUTIONS, MODELS, SAMPLERS, UC_PRESETS, NOISE_SCHEDULES
from keyboards import (
    main_menu as base_main_menu, settings_menu, model_menu, size_menu, sampler_menu,
    uc_menu, numeric_menu, modes_menu, presets_menu, pending_prompt_menu,
    after_generation_menu, noise_menu
)
from app.services.nai_client import NovelAIClient, NovelAIError
from prompt_tools import natural_to_nai_tags, looks_like_english_tags
from storage import (
    get_settings, save_settings, patch_settings, add_history, get_history,
    add_favorite, get_favorites
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

TMP_DIR = Path("data/tmp_images")
TMP_DIR.mkdir(parents=True, exist_ok=True)

def main_menu():
    return base_main_menu(CHANNEL_URL)

SAFE_RESOLUTIONS = {(832, 1216), (1216, 832), (1024, 1024), (768, 1344), (1344, 768), (512, 768)}
ANLAS_WARNING = "Это может тратить Anlas. Включи 💎 PRO / Анласы, если хочешь разрешить дорогие режимы."

def prompt_preview_text(prompt: str, original: str = "") -> str:
    if original and original.strip() and original.strip() != prompt.strip():
        return (
            "📝 <b>Промт готов. Запускаем?</b>\n\n"
            "<b>Исходник:</b>\n"
            f"<code>{html.escape(original[:1400])}</code>\n\n"
            "<b>Теговый промт:</b>\n"
            f"<code>{html.escape(prompt[:3000])}</code>"
        )
    return (
        "📝 <b>Промт готов. Запускаем?</b>\n\n"
        f"<code>{html.escape(prompt[:3000])}</code>"
    )

def apply_anlas_safe_defaults(user_id: int):
    s = get_settings(user_id)
    if s.pro_mode:
        return s
    updates = {}
    if s.n_samples != 1:
        updates["n_samples"] = 1
    if (s.width, s.height) not in SAFE_RESOLUTIONS:
        updates.update({"width": 832, "height": 1216})
    if updates:
        s = patch_settings(user_id, **updates)
    return s

async def show_pending_prompt(message: types.Message, user_id: int) -> None:
    s = get_settings(user_id)
    if not s.pending_prompt:
        await message.answer("📝 Черновик пуст. Пришли новый промт обычным сообщением.", reply_markup=main_menu())
        return
    await message.answer(
        prompt_preview_text(s.pending_prompt, s.pending_original_prompt),
        parse_mode="HTML",
        reply_markup=pending_prompt_menu(bool(s.pending_image_path)),
    )

def settings_text(user_id: int) -> str:
    s = get_settings(user_id)
    return (
        "⚙️ <b>Текущие настройки</b>\n\n"
        f"Модель: <code>{s.model_name}</code>\n"
        f"Размер: <code>{s.width}x{s.height}</code>\n"
        f"Режим: <code>{'PRO' if s.pro_mode else 'Экономный'}</code>\n"
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
        f"CFG rescale: <code>{s.cfg_rescale}</code>\n"
        f"Noise schedule: <code>{s.noise_schedule}</code>\n"
        f"Img2Img: <code>{s.img2img_strength} / {s.img2img_noise}</code>\n"
        f"Промт переведён из русского: <code>{bool(s.pending_original_prompt and s.pending_original_prompt != s.pending_prompt)}</code>"
    )

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
    await message.answer(
        "🦝 <b>NovelAI bot</b>\n\n"
        "Привет! Я помогу быстро сделать картинку в NovelAI.\n\n"
        "• 🎨 <b>Новый промт</b> — напиши идею своими словами\n"
        "• ⚡ <b>Быстрые пресеты</b> — готовые идеи в один тап\n"
        "• 🔁 <b>Повторить</b> — перегенерировать последний промт\n\n"
        "Команда тоже работает:\n"
        "<code>/gen raccoon girl, pink eyes, sketch, ruins</code>\n"
        "<code>/draw raccoon girl, pink eyes, sketch, ruins</code>",
        reply_markup=main_menu(),
        parse_mode="HTML",
    )

@dp.message(Command("help"))
async def help_cmd(message: types.Message):
    await message.answer(
        "Команды:\n"
        "/gen prompt — сгенерировать\n"
        "/draw prompt — сгенерировать\n"
        "/settings — настройки\n"
        "/presets — быстрые пресеты\n"
        "/last_prompt — показать последний промт\n"
        "/retry — повторить последний промт\n"
        "/seed random или /seed 123456 — задать seed\n"
        "/raw — показать настройки\n"
        "/nai_debug — показать модель и параметры NovelAI\n"
        "/cancel — отменить ввод промта\n\n"
        "Можно не писать /gen: отправь обычный текст, я покажу черновик и кнопки подтверждения.\n"
        "Для img2img: отправь картинку, потом ответь на неё командой /gen prompt."
    )

@dp.message(Command("generation_settings"))
async def generation_settings_cmd(message: types.Message):
    await message.answer(settings_text(message.from_user.id), reply_markup=settings_menu(), parse_mode="HTML")

@dp.message(Command("settings"))
async def settings_cmd(message: types.Message):
    await message.answer(settings_text(message.from_user.id), reply_markup=settings_menu(), parse_mode="HTML")

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
    if ADMIN_IDS and user_id not in ADMIN_IDS:
        await message.answer("⛔ Генерация доступна только администраторам.", reply_markup=main_menu())
        return

    s = patch_settings(user_id, last_prompt=prompt)
    s = apply_anlas_safe_defaults(user_id)

    if actor is None:
        await notify_admins_about_prompt(message, prompt)

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

    try:
        images = await nai.generate(prompt, s, image_bytes=image_bytes)
        add_history(user_id, {"prompt": prompt, "seed": s.seed, "model": s.model_name, "size": f"{s.width}x{s.height}", "timestamp": datetime.now(timezone.utc).isoformat()})
        patch_settings(user_id, pending_image_path="")
        await wait.delete()

        for idx, img in enumerate(images, start=1):
            name = f"novelai_{idx}.png"
            image = BufferedInputFile(img, filename=name)
            caption = f"✅ <b>Готово</b>\\n<code>{html.escape(prompt[:900])}</code>"
            try:
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
    await call.message.edit_text(settings_text(call.from_user.id), reply_markup=settings_menu(), parse_mode="HTML")
    await call.answer()

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

@dp.callback_query(F.data == "menu:help")
async def cb_help(call: types.CallbackQuery):
    await call.message.edit_text(
        "❔ <b>Помощь</b>\n\n"
        "• /gen prompt и /draw prompt — генерация\n"
        "• /presets — готовые промты\n"
        "• /last_prompt — показать последний промт\n"
        "• /retry — повторить последний промт\n"
        "• /seed random или /seed 123456 — seed\n"
        "• /settings — меню настроек\n"
        "• reply на фото + /gen prompt — img2img\n\n"
        "Подсказка: если результат почти понравился, задай фиксированный seed и меняй промт маленькими шагами.",
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
        reply_markup=pending_prompt_menu(bool(current.pending_image_path)),
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
        prompt_preview_text(s.pending_prompt, s.pending_original_prompt),
        parse_mode="HTML",
        reply_markup=pending_prompt_menu(bool(s.pending_image_path)),
    )
    await call.answer("Показываю исходник")

@dp.callback_query(F.data == "prompt:confirm")
async def cb_prompt_confirm(call: types.CallbackQuery):
    s = get_settings(call.from_user.id)
    prompt = s.pending_prompt.strip()
    if not prompt:
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

@dp.callback_query(F.data == "settings:model")
async def cb_model(call: types.CallbackQuery):
    await call.message.edit_text("Выбери модель:", reply_markup=model_menu())
    await call.answer()

@dp.callback_query(F.data == "settings:size")
async def cb_size(call: types.CallbackQuery):
    await call.message.edit_text("Выбери размер:", reply_markup=size_menu())
    await call.answer()

@dp.callback_query(F.data == "settings:sampler")
async def cb_sampler(call: types.CallbackQuery):
    await call.message.edit_text("Выбери sampler:", reply_markup=sampler_menu())
    await call.answer()

@dp.callback_query(F.data == "settings:uc")
async def cb_uc(call: types.CallbackQuery):
    await call.message.edit_text("Выбери UC preset:", reply_markup=uc_menu())
    await call.answer()

@dp.callback_query(F.data == "settings:n")
async def cb_n(call: types.CallbackQuery):
    await call.message.edit_text("Сколько картинок за раз?", reply_markup=numeric_menu("n", ["1", "2", "3", "4"]))
    await call.answer()

@dp.callback_query(F.data == "settings:steps")
async def cb_steps(call: types.CallbackQuery):
    await call.message.edit_text("Steps:", reply_markup=numeric_menu("steps", ["10", "18", "23", "28", "32", "40"]))
    await call.answer()

@dp.callback_query(F.data == "settings:scale")
async def cb_scale(call: types.CallbackQuery):
    await call.message.edit_text("Guidance / scale:", reply_markup=numeric_menu("scale", ["2.5", "3", "4", "5", "6", "7"]))
    await call.answer()

@dp.callback_query(F.data == "settings:seed")
async def cb_seed(call: types.CallbackQuery):
    await call.message.edit_text("Seed:", reply_markup=numeric_menu("seed", ["-1", "1", "42", "12345", "777", "999999"]))
    await call.answer()

@dp.callback_query(F.data == "settings:negative")
async def cb_negative(call: types.CallbackQuery):
    await call.message.edit_text(
        "🚫 Чтобы задать negative prompt, напиши:\n"
        "<code>/negative bad hands, extra fingers</code>",
        reply_markup=settings_menu(),
        parse_mode="HTML",
    )
    await call.answer()

@dp.callback_query(F.data == "settings:cfg")
async def cb_cfg(call: types.CallbackQuery):
    await call.message.edit_text("CFG rescale:", reply_markup=numeric_menu("cfg", ["0", "0.2", "0.4", "0.6", "0.8", "1.0"]))
    await call.answer()

@dp.callback_query(F.data == "settings:noise")
async def cb_noise(call: types.CallbackQuery):
    await call.message.edit_text("Noise schedule:", reply_markup=noise_menu())
    await call.answer()

@dp.callback_query(F.data == "settings:img2img")
async def cb_img2img_settings(call: types.CallbackQuery):
    await call.message.edit_text("Img2Img strength / noise:", reply_markup=numeric_menu("img2img", ["0.35/0.05", "0.50/0.10", "0.65/0.15", "0.75/0.20"]))
    await call.answer()

@dp.callback_query(F.data == "settings:modes")
async def cb_modes(call: types.CallbackQuery):
    s = get_settings(call.from_user.id)
    await call.message.edit_text(
        "🦝 Режимы:",
        reply_markup=modes_menu(s.furry_mode, s.background_mode, s.add_quality_tags)
    )
    await call.answer()




def _history_lines(items: list[dict], title: str) -> str:
    if not items:
        return f"{title}\n\nПока пусто. Сгенерируй картинку и возвращайся сюда 🦝"
    lines = [title, ""]
    for i, item in enumerate(items[:10], start=1):
        prompt = html.escape(str(item.get("prompt", ""))[:180])
        lines.append(
            f"{i}. <code>{prompt}</code>\n"
            f"   🎲 <code>{item.get('seed', '—')}</code> · "
            f"🧠 <code>{html.escape(str(item.get('model', '—')))}</code> · "
            f"📐 <code>{item.get('size', '—')}</code>"
        )
    return "\n".join(lines)

def _last_generation_item(user_id: int) -> dict | None:
    s = get_settings(user_id)
    if not s.last_prompt:
        return None
    return {
        "prompt": s.last_prompt,
        "seed": s.seed,
        "model": s.model_name,
        "size": f"{s.width}x{s.height}",
        "timestamp": datetime.now(timezone.utc).isoformat(),
    }

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
        "improve": "best composition, expressive lighting, detailed background, cohesive color palette, sharp focus",
        "raccoon": "ArtRaccoon vibe, cozy mischievous raccoon energy, warm cinematic light, whimsical details",
        "aelita": "Aelita, elegant retro-futuristic princess from Mars, silver crown, red desert palace",
    }
    return f"{p}, {additions.get(tool, '')}".strip(", ")

@dp.message(Command("history"))
async def history_cmd(message: types.Message):
    await message.answer(_history_lines(get_history(message.from_user.id), "🕘 <b>История генераций</b>"), parse_mode="HTML", reply_markup=main_menu())

@dp.message(Command("favorites"))
async def favorites_cmd(message: types.Message):
    await message.answer(_history_lines(get_favorites(message.from_user.id), "⭐ <b>Избранное</b>"), parse_mode="HTML", reply_markup=main_menu())

@dp.callback_query(F.data == "menu:history")
async def cb_history(call: types.CallbackQuery):
    await call.message.edit_text(_history_lines(get_history(call.from_user.id), "🕘 <b>История генераций</b>"), parse_mode="HTML", reply_markup=main_menu())
    await call.answer()

@dp.callback_query(F.data == "menu:favorites")
async def cb_favorites(call: types.CallbackQuery):
    await call.message.edit_text(_history_lines(get_favorites(call.from_user.id), "⭐ <b>Избранное</b>"), parse_mode="HTML", reply_markup=main_menu())
    await call.answer()

@dp.callback_query(F.data == "favorite:last")
async def cb_favorite_last(call: types.CallbackQuery):
    item = _last_generation_item(call.from_user.id)
    if not item:
        await call.answer("Пока нечего добавить", show_alert=True)
        return
    add_favorite(call.from_user.id, item)
    await call.answer("Добавлено в избранное ⭐")

@dp.callback_query(F.data.in_({"menu:inpaint", "menu:reference", "menu:upscale"}))
async def cb_placeholders(call: types.CallbackQuery):
    if not get_settings(call.from_user.id).pro_mode:
        await call.message.edit_text("💎 Это PRO/Anlas-связанная функция. Сейчас она заблокирована в экономном режиме: может тратить Anlas. Включи 💎 PRO / Анласы, чтобы разрешить дорогие режимы.", reply_markup=main_menu())
    else:
        texts = {
            "menu:inpaint": "🩹 Inpaint — PRO/Anlas-связанная функция. Поддержка масок будет добавлена позже.",
            "menu:reference": "🧬 Reference — PRO/Anlas-связанная функция. Workflow будет добавлен позже.",
            "menu:upscale": "🔍 Upscale / Enhance — PRO/Anlas-связанная функция и будет добавлена следующим безопасным шагом.",
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
    prompt = transform_prompt(source if tool == "translate" else s.pending_prompt, tool)
    patch_settings(call.from_user.id, pending_prompt=prompt, pending_original_prompt=source if tool == "translate" else s.pending_original_prompt)
    await call.message.edit_text(prompt_preview_text(prompt, source if tool == "translate" else s.pending_original_prompt), parse_mode="HTML", reply_markup=pending_prompt_menu(bool(s.pending_image_path)))
    await call.answer("Промт обновлён")

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


@dp.message(GenState.waiting_prompt)
async def gen_from_button(message: types.Message, state: FSMContext):
    prompt = message.text.strip() if message.text else ""

    if not prompt:
        await message.answer("Пришли текстовый промт или нажми /cancel.")
        return

    await state.clear()
    converted = natural_to_nai_tags(prompt)
    original = "" if converted == prompt and looks_like_english_tags(prompt) else prompt
    patch_settings(message.from_user.id, pending_prompt=converted, pending_original_prompt=original, prompt_action="")
    await show_pending_prompt(message, message.from_user.id)


@dp.message(Command("negative"))
async def negative_cmd(message: types.Message):
    text = message.text.replace("/negative", "", 1).strip()
    patch_settings(message.from_user.id, negative_prompt=text)
    await message.answer("🚫 Негативный промт обновлён.", reply_markup=settings_menu())

@dp.callback_query(F.data.startswith("set:model:"))
async def set_model(call: types.CallbackQuery):
    name = call.data.split(":", 2)[2]
    if name not in MODELS:
        await call.answer("Неизвестная модель", show_alert=True)
        return
    patch_settings(call.from_user.id, model_name=name)
    await call.message.edit_text(settings_text(call.from_user.id), reply_markup=settings_menu(), parse_mode="HTML")
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
        patch_settings(call.from_user.id, width=w, height=h)
    await call.message.edit_text(settings_text(call.from_user.id), reply_markup=settings_menu(), parse_mode="HTML")
    await call.answer("Размер обновлён")

@dp.callback_query(F.data.startswith("set:sampler:"))
async def set_sampler(call: types.CallbackQuery):
    sampler = call.data.split(":", 2)[2]
    if sampler not in SAMPLERS:
        await call.answer("Неизвестный sampler", show_alert=True)
        return
    patch_settings(call.from_user.id, sampler=sampler)
    await call.message.edit_text(settings_text(call.from_user.id), reply_markup=settings_menu(), parse_mode="HTML")
    await call.answer("Sampler обновлён")

@dp.callback_query(F.data.startswith("set:uc:"))
async def set_uc(call: types.CallbackQuery):
    uc = call.data.split(":", 2)[2]
    if uc not in UC_PRESETS:
        await call.answer("Неизвестный UC preset", show_alert=True)
        return
    patch_settings(call.from_user.id, uc_preset=uc)
    await call.message.edit_text(settings_text(call.from_user.id), reply_markup=settings_menu(), parse_mode="HTML")
    await call.answer("UC обновлён")

@dp.callback_query(F.data.startswith("set:n:"))
async def set_n(call: types.CallbackQuery):
    val = int(call.data.split(":", 2)[2])
    if val > 1 and not get_settings(call.from_user.id).pro_mode:
        patch_settings(call.from_user.id, n_samples=1)
        await call.message.edit_text(settings_text(call.from_user.id), reply_markup=settings_menu(), parse_mode="HTML")
        await call.answer(ANLAS_WARNING, show_alert=True)
        return
    patch_settings(call.from_user.id, n_samples=val)
    await call.message.edit_text(settings_text(call.from_user.id), reply_markup=settings_menu(), parse_mode="HTML")
    await call.answer("Количество обновлено")

@dp.callback_query(F.data.startswith("set:steps:"))
async def set_steps(call: types.CallbackQuery):
    val = int(call.data.split(":", 2)[2])
    patch_settings(call.from_user.id, steps=val)
    await call.message.edit_text(settings_text(call.from_user.id), reply_markup=settings_menu(), parse_mode="HTML")
    await call.answer("Steps обновлены")

@dp.callback_query(F.data.startswith("set:scale:"))
async def set_scale(call: types.CallbackQuery):
    val = float(call.data.split(":", 2)[2])
    patch_settings(call.from_user.id, scale=val)
    await call.message.edit_text(settings_text(call.from_user.id), reply_markup=settings_menu(), parse_mode="HTML")
    await call.answer("Scale обновлён")

@dp.callback_query(F.data.startswith("set:seed:"))
async def set_seed(call: types.CallbackQuery):
    val = int(call.data.split(":", 2)[2])
    patch_settings(call.from_user.id, seed=val)
    await call.message.edit_text(settings_text(call.from_user.id), reply_markup=settings_menu(), parse_mode="HTML")
    await call.answer("Seed обновлён")


@dp.callback_query(F.data.startswith("set:cfg:"))
async def set_cfg(call: types.CallbackQuery):
    try:
        val = max(0.0, min(1.0, float(call.data.split(":", 2)[2])))
    except ValueError:
        await call.answer("Некорректное значение", show_alert=True)
        return
    patch_settings(call.from_user.id, cfg_rescale=val)
    await call.message.edit_text(settings_text(call.from_user.id), reply_markup=settings_menu(), parse_mode="HTML")
    await call.answer("CFG rescale обновлён")

@dp.callback_query(F.data.startswith("set:noise:"))
async def set_noise(call: types.CallbackQuery):
    val = call.data.split(":", 2)[2]
    if val not in NOISE_SCHEDULES:
        await call.answer("Неизвестный noise schedule", show_alert=True)
        return
    patch_settings(call.from_user.id, noise_schedule=val)
    await call.message.edit_text(settings_text(call.from_user.id), reply_markup=settings_menu(), parse_mode="HTML")
    await call.answer("Noise schedule обновлён")

@dp.callback_query(F.data.startswith("set:img2img:"))
async def set_img2img(call: types.CallbackQuery):
    raw = call.data.split(":", 2)[2]
    try:
        strength, noise = [float(x) for x in raw.split("/", 1)]
    except ValueError:
        await call.answer("Некорректное значение", show_alert=True)
        return
    patch_settings(call.from_user.id, img2img_strength=strength, img2img_noise=noise)
    await call.message.edit_text(settings_text(call.from_user.id), reply_markup=settings_menu(), parse_mode="HTML")
    await call.answer("Img2Img обновлён")


@dp.callback_query(F.data == "toggle:pro")
async def toggle_pro(call: types.CallbackQuery):
    s = get_settings(call.from_user.id)
    new_value = not s.pro_mode
    updates = {"pro_mode": new_value}
    if not new_value:
        updates["n_samples"] = 1
        if (s.width, s.height) not in SAFE_RESOLUTIONS:
            updates.update({"width": 832, "height": 1216})
    patch_settings(call.from_user.id, **updates)
    await call.message.edit_text(settings_text(call.from_user.id), reply_markup=settings_menu(), parse_mode="HTML")
    await call.answer("PRO / Анласы включены" if new_value else "Экономный режим включён")

@dp.callback_query(F.data == "toggle:furry")
async def toggle_furry(call: types.CallbackQuery):
    s = get_settings(call.from_user.id)
    patch_settings(call.from_user.id, furry_mode=not s.furry_mode)
    s = get_settings(call.from_user.id)
    await call.message.edit_text("🦝 Режимы:", reply_markup=modes_menu(s.furry_mode, s.background_mode, s.add_quality_tags))
    await call.answer()

@dp.callback_query(F.data == "toggle:background")
async def toggle_background(call: types.CallbackQuery):
    s = get_settings(call.from_user.id)
    patch_settings(call.from_user.id, background_mode=not s.background_mode)
    s = get_settings(call.from_user.id)
    await call.message.edit_text("🦝 Режимы:", reply_markup=modes_menu(s.furry_mode, s.background_mode, s.add_quality_tags))
    await call.answer()

@dp.callback_query(F.data == "toggle:quality")
async def toggle_quality(call: types.CallbackQuery):
    s = get_settings(call.from_user.id)
    patch_settings(call.from_user.id, add_quality_tags=not s.add_quality_tags)
    s = get_settings(call.from_user.id)
    await call.message.edit_text("🦝 Режимы:", reply_markup=modes_menu(s.furry_mode, s.background_mode, s.add_quality_tags))
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
        prompt = natural_to_nai_tags(original)
        patch_settings(message.from_user.id, pending_prompt=prompt, pending_original_prompt=original, prompt_action="")
        await message.answer("✏️ Добавила текст к черновику.")
    else:
        converted = natural_to_nai_tags(text)
        original = "" if converted == text and looks_like_english_tags(text) else text
        patch_settings(message.from_user.id, pending_prompt=converted, pending_original_prompt=original, prompt_action="")
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
