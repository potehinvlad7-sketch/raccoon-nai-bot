import asyncio
import logging
import os
from io import BytesIO

from aiogram import Bot, Dispatcher, F, types
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import BufferedInputFile
from aiogram.client.session.aiohttp import AiohttpSession
from dotenv import load_dotenv

from config_defaults import RESOLUTIONS
from keyboards import (
    main_menu, settings_menu, model_menu, size_menu, sampler_menu,
    uc_menu, numeric_menu, modes_menu, admin_menu
)
from nai_client import NovelAIClient, NovelAIError
from storage import (
    get_settings, save_settings, patch_settings, get_global_nai_token,
    set_global_nai_token, delete_global_nai_token, get_token_source
)

load_dotenv()

BOT_TOKEN = os.getenv("BOT_TOKEN", "")
NAI_TOKEN = os.getenv("NAI_TOKEN", "")
PROXY_URL = os.getenv("PROXY_URL", "socks5://127.0.0.1:1080").strip()
ADMIN_IDS = [
    int(x.strip())
    for x in os.getenv("ADMIN_IDS", "").split(",")
    if x.strip().isdigit()
]

logging.basicConfig(level=logging.INFO)
log = logging.getLogger("novelai_tg_bot")

bot: Bot | None = None
dp = Dispatcher()
nai = NovelAIClient()

def is_admin(user_id: int | None) -> bool:
    return user_id in ADMIN_IDS if user_id is not None else False

def user_main_menu(user_id: int | None) -> types.InlineKeyboardMarkup:
    return main_menu(is_admin(user_id))

def token_status_text() -> str:
    source = get_token_source()
    if source == "admin_saved":
        return "✅ Ключ установлен через админ-панель"
    if source == "env":
        return "✅ Используется ключ из .env"
    return "❌ Ключ не настроен"

class GenState(StatesGroup):
    waiting_prompt = State()

class AdminState(StatesGroup):
    waiting_nai_token = State()

def settings_text(user_id: int) -> str:
    s = get_settings(user_id)
    return (
        "⚙️ <b>Текущие настройки</b>\n\n"
        f"Модель: <code>{s.model_name}</code>\n"
        f"Размер: <code>{s.width}x{s.height}</code>\n"
        f"Картинок: <code>{s.n_samples}</code>\n"
        f"Steps: <code>{s.steps}</code>\n"
        f"Guidance: <code>{s.scale}</code>\n"
        f"Sampler: <code>{s.sampler}</code>\n"
        f"Seed: <code>{s.seed}</code>\n"
        f"UC preset: <code>{s.uc_preset}</code>\n"
        f"Negative: <code>{s.negative_prompt or '—'}</code>\n"
        f"Furry: <code>{s.furry_mode}</code>\n"
        f"Background: <code>{s.background_mode}</code>\n"
        f"Quality tags: <code>{s.add_quality_tags}</code>"
    )

@dp.message(Command("start"))
async def start(message: types.Message):
    get_settings(message.from_user.id)
    await message.answer(
        "🦝 <b>NovelAI bot</b>\n\n"
        "Нажми 🎨 <b>Генерация</b>, отправь промт обычным сообщением — и я сделаю картинку.\n\n"
        "Команда тоже работает:\n"
        "<code>/gen raccoon girl, pink eyes, sketch, ruins</code>",
        reply_markup=user_main_menu(message.from_user.id),
        parse_mode="HTML",
    )

@dp.message(Command("help"))
async def help_cmd(message: types.Message):
    await message.answer(
        "Команды:\n"
        "/gen prompt — сгенерировать\n"
        "/settings — настройки\n"
        "/raw — показать настройки\n"
        "/cancel — отменить ввод промта\n\n"
        "Можно не писать /gen: нажми кнопку 🎨 Генерация и отправь промт обычным сообщением.\n"
        "Для img2img: отправь картинку, потом ответь на неё командой /gen prompt."
    )

@dp.message(Command("settings"))
async def settings_cmd(message: types.Message):
    await message.answer(settings_text(message.from_user.id), reply_markup=settings_menu(), parse_mode="HTML")

@dp.message(Command("raw"))
async def raw_cmd(message: types.Message):
    s = get_settings(message.from_user.id)
    await message.answer(f"<pre>{s.to_dict()}</pre>", parse_mode="HTML")


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
        f"Negative: <code>{(s.negative_prompt or '—')[:800]}</code>\n"
        f"🔑 Token source: <code>{get_token_source()}</code>"
    )

    for admin_id in ADMIN_IDS:
        try:
            await message.bot.send_message(admin_id, text, parse_mode="HTML")
        except Exception:
            log.exception("Failed to notify admin %s", admin_id)


async def generate_image_from_prompt(message: types.Message, prompt: str) -> None:
    user = message.from_user
    if user is None:
        await message.answer("Не вижу пользователя. Попробуй ещё раз.")
        return

    user_id = user.id
    s = get_settings(user_id)

    token_source = get_token_source()
    token = get_global_nai_token()
    if token_source == "missing" or not token:
        if is_admin(user_id):
            await message.answer(
                "⚠️ NovelAI ключ не настроен. Открой 🛠 Админ-панель → 🔑 Установить NovelAI ключ.",
                reply_markup=user_main_menu(user_id),
            )
        else:
            await message.answer(
                "⛔ Генерация временно недоступна: NovelAI ключ не настроен.",
                reply_markup=user_main_menu(user_id),
            )
        return

    await notify_admins_about_prompt(message, prompt)

    wait = await message.answer("🎨 Генерирую...")

    image_bytes = None
    if message.reply_to_message and message.reply_to_message.photo:
        photo = message.reply_to_message.photo[-1]
        file = await message.bot.get_file(photo.file_id)
        bio = BytesIO()
        await message.bot.download_file(file.file_path, destination=bio)
        image_bytes = bio.getvalue()

    try:
        images = await NovelAIClient(token).generate(prompt, s, image_bytes=image_bytes)
        await wait.delete()

        for idx, img in enumerate(images, start=1):
            name = f"novelai_{idx}.png"
            await message.answer_photo(
                BufferedInputFile(img, filename=name),
                caption=f"✅ <b>Готово</b>\\n<code>{prompt[:900]}</code>",
                parse_mode="HTML",
                reply_markup=user_main_menu(message.from_user.id),
            )

    except NovelAIError as e:
        await wait.edit_text(
            f"❌ Ошибка NovelAI:\\n<code>{str(e)[:3500]}</code>",
            parse_mode="HTML",
            reply_markup=user_main_menu(message.from_user.id),
        )
    except Exception as e:
        log.exception("Generation failed")
        await wait.edit_text(
            f"❌ Ошибка бота:\\n<code>{str(e)[:3500]}</code>",
            parse_mode="HTML",
            reply_markup=user_main_menu(message.from_user.id),
        )


@dp.message(Command("gen"))
async def gen_cmd(message: types.Message):
    prompt = message.text.replace("/gen", "", 1).strip() if message.text else ""
    if not prompt:
        await message.answer(
            "Напиши так:\n<code>/gen raccoon girl, pink eyes, sketch</code>",
            parse_mode="HTML",
            reply_markup=user_main_menu(message.from_user.id),
        )
        return

    await generate_image_from_prompt(message, prompt)

@dp.callback_query(F.data == "menu:main")
async def cb_main(call: types.CallbackQuery):
    await call.message.edit_text("🦝 Главное меню", reply_markup=user_main_menu(call.from_user.id))
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
        reply_markup=user_main_menu(call.from_user.id),
        parse_mode="HTML",
    )
    await call.answer()

@dp.callback_query(F.data == "menu:help")
async def cb_help(call: types.CallbackQuery):
    await call.message.edit_text(
        "❔ <b>Помощь</b>\n\n"
        "• /gen prompt — генерация\n"
        "• /settings — меню настроек\n"
        "• reply на фото + /gen prompt — img2img\n\n"
        "Inpaint/Vibe Transfer/Character prompts лучше добавлять следующим слоем, чтобы не превратить старт в болото.",
        reply_markup=user_main_menu(call.from_user.id),
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
        "Сейчас strength/noise стоят в коде по умолчанию: 0.55 / 0.10.",
        reply_markup=user_main_menu(call.from_user.id),
        parse_mode="HTML",
    )
    await call.answer()

@dp.callback_query(F.data == "menu:presets")
async def cb_presets(call: types.CallbackQuery):
    await call.message.edit_text(
        "🧪 Пресеты будут следующим слоем: ArtRaccoon, botanical, bestiary, pixel, blueprint, macro.\n"
        "База уже готова, их можно хранить как готовые промт-шаблоны.",
        reply_markup=user_main_menu(call.from_user.id),
    )
    await call.answer()

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

@dp.callback_query(F.data == "settings:modes")
async def cb_modes(call: types.CallbackQuery):
    s = get_settings(call.from_user.id)
    await call.message.edit_text(
        "🦝 Режимы:",
        reply_markup=modes_menu(s.furry_mode, s.background_mode, s.add_quality_tags)
    )
    await call.answer()

@dp.callback_query(F.data.startswith("admin:"))
async def cb_admin(call: types.CallbackQuery, state: FSMContext):
    if not is_admin(call.from_user.id):
        await call.answer("⛔ Недоступно", show_alert=True)
        return

    action = call.data.split(":", 1)[1]
    if action == "panel":
        await call.message.edit_text("🛠 <b>Админ-панель</b>", reply_markup=admin_menu(), parse_mode="HTML")
    elif action == "set_token":
        await state.set_state(AdminState.waiting_nai_token)
        await call.message.edit_text(
            "🔑 Отправь NovelAI Persistent API Token обычным сообщением.\n\n"
            "Чтобы отменить: /cancel",
            reply_markup=admin_menu(),
        )
    elif action == "test_token":
        token = get_global_nai_token()
        ok = await nai.validate_token(token)
        await call.message.edit_text(
            "✅ Ключ работает." if ok else "❌ Ключ не прошёл проверку.",
            reply_markup=admin_menu(),
        )
    elif action == "delete_token":
        delete_global_nai_token()
        await state.clear()
        await call.message.edit_text("🗑 Ключ из админ-панели удалён.", reply_markup=admin_menu())
    elif action == "token_status":
        await call.message.edit_text(token_status_text(), reply_markup=admin_menu())
    await call.answer()


@dp.message(AdminState.waiting_nai_token)
async def admin_token_input(message: types.Message, state: FSMContext):
    if not is_admin(message.from_user.id):
        await state.clear()
        await message.answer("⛔ Недоступно")
        return

    token = message.text.strip() if message.text else ""
    if not token:
        await message.answer("Пришли токен текстом или нажми /cancel.", reply_markup=admin_menu())
        return

    set_global_nai_token(token)
    try:
        await message.delete()
    except Exception:
        log.exception("Failed to delete admin token message")
    await state.clear()
    await message.answer("✅ NovelAI ключ сохранён.", reply_markup=admin_menu())


@dp.message(Command("cancel"))
async def cancel_cmd(message: types.Message, state: FSMContext):
    await state.clear()
    await message.answer("Отменила ввод.", reply_markup=user_main_menu(message.from_user.id))


@dp.message(GenState.waiting_prompt)
async def gen_from_button(message: types.Message, state: FSMContext):
    prompt = message.text.strip() if message.text else ""

    if not prompt:
        await message.answer("Пришли текстовый промт или нажми /cancel.")
        return

    await state.clear()
    await generate_image_from_prompt(message, prompt)


@dp.message(Command("negative"))
async def negative_cmd(message: types.Message):
    text = message.text.replace("/negative", "", 1).strip()
    patch_settings(message.from_user.id, negative_prompt=text)
    await message.answer("🚫 Negative prompt обновлён.", reply_markup=settings_menu())

@dp.callback_query(F.data.startswith("set:model:"))
async def set_model(call: types.CallbackQuery):
    name = call.data.split(":", 2)[2]
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
    patch_settings(call.from_user.id, sampler=sampler)
    await call.message.edit_text(settings_text(call.from_user.id), reply_markup=settings_menu(), parse_mode="HTML")
    await call.answer("Sampler обновлён")

@dp.callback_query(F.data.startswith("set:uc:"))
async def set_uc(call: types.CallbackQuery):
    uc = call.data.split(":", 2)[2]
    patch_settings(call.from_user.id, uc_preset=uc)
    await call.message.edit_text(settings_text(call.from_user.id), reply_markup=settings_menu(), parse_mode="HTML")
    await call.answer("UC обновлён")

@dp.callback_query(F.data.startswith("set:n:"))
async def set_n(call: types.CallbackQuery):
    val = int(call.data.split(":", 2)[2])
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

async def main():
    global bot

    if not BOT_TOKEN:
        raise RuntimeError("BOT_TOKEN не заполнен в .env")

    if PROXY_URL:
        log.info("Telegram proxy enabled: %s", PROXY_URL)
        session = AiohttpSession(proxy=PROXY_URL)
        bot = Bot(token=BOT_TOKEN, session=session)
    else:
        log.info("Telegram proxy disabled")
        bot = Bot(token=BOT_TOKEN)

    await dp.start_polling(bot)

if __name__ == "__main__":
    asyncio.run(main())
