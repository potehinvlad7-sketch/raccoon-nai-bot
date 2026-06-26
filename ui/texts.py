"""Reusable Telegram text builders."""

import html
from app.services.nai_client import payload_summary
from config_defaults import QUICK_PRESETS
from prompt_tools import has_unknown_russian


PAID_PLACEHOLDER_TEXT = "💎 Эта функция появится в платном режиме.\nСкоро добавим покупку генераций и расширенные настройки."
DAILY_LIMIT_TEXT = "🕯️ На сегодня бесплатные генерации закончились.\nЗавтра появятся новые 10 попыток."
GENERATION_STARTED_TEXT = "✨ Запускаю генерацию. Енот уже смешивает пиксели..."
PROMPT_EMPTY_TEXT = "🖼️ Черновик пока пуст. Пришли идею картинки обычным сообщением."
CANCEL_TEXT = "❌ Черновик очищен. Возвращаю в главное меню."
CLEAR_TEXT = "🧹 Черновик очищен. Можно прислать новую идею."
EDIT_PROMPT_TEXT = "✏️ Пришли новый текст — я обновлю черновик."


def cooldown_text(seconds: int) -> str:
    return f"⏳ Дай еноту пару секунд отдышаться.\nОсталось: {seconds}s"


def start_text(remaining: int | None, daily_limit: int, is_admin: bool = False) -> str:
    remaining_line = f"\n\nСегодня осталось: <b>{remaining}/{daily_limit}</b>." if remaining is not None else ""
    admin_line = "\n\nАдмин-панель и специальные команды доступны как раньше." if is_admin else ""
    return (
        "🦝 <b>RaccoonNAI</b>\n\n"
        "Привет! Я помогу превратить идею в картинку.\n"
        "Напиши промпт — покажу красивый черновик перед генерацией."
        + remaining_line
        + admin_line
    )


def howto_text(remaining: int | None = None, daily_limit: int = 10) -> str:
    remaining_line = f"\n\nСегодня осталось: {remaining}/{daily_limit}." if remaining is not None else ""
    return (
        "❓ <b>Помощь RaccoonNAI</b>\n\n"
        "• Напиши идею картинки одним сообщением.\n"
        "• Проверь черновик и нажми ✅ Генерировать.\n"
        "• ✏️ можно поправить, 🧹 очистить, ❌ отменить.\n"
        "• Бесплатно: 10 генераций в день. ✨"
        + remaining_line
    )


def main_menu_text() -> str:
    return "🦝 <b>Главное меню</b>\n\nЧто рисуем дальше?"


def prompt_request_text() -> str:
    return (
        "🎨 <b>Новый промпт</b>\n\n"
        "Опиши картинку как тебе удобно — коротко или с деталями.\n\n"
        "Например:\n"
        "<code>девушка с ушками енота, розовые глаза, древние руины</code>\n\n"
        "Отмена: /cancel"
    )


def generation_result_caption(model: str, width: int, height: int, seed: int) -> str:
    seed_text = "random" if seed == -1 else str(seed)
    return (
        "✅ <b>Готово</b>\n"
        f"🧠 <code>{html.escape(str(model))}</code>\n"
        f"📐 <code>{width}x{height}</code>\n"
        f"🎲 Seed: <code>{html.escape(seed_text)}</code>"
    )


def nai_payload_summary_text(payload: dict, settings) -> str:
    summary = payload_summary(payload, settings)
    lines = ["🧪 <b>NovelAI payload summary</b>"]
    for key, value in summary.items():
        lines.append(f"<b>{html.escape(str(key))}:</b> <code>{html.escape(str(value))[:1200]}</code>")
    return "\n".join(lines)


def generation_settings_summary(s) -> str:
    negative = (s.negative_prompt or "").strip()
    negative = "empty" if not negative else html.escape(negative[:120])
    seed = "random" if s.seed == -1 else str(s.seed)
    return f"📐 Размер: <code>{s.width}x{s.height}</code>\n👣 Шаги: <code>{s.steps}</code>\n🧲 CFG: <code>{s.scale}</code>\n🎲 Seed: <code>{seed}</code>\n🚫 Негатив: <code>{negative}</code>\n🧠 Модель: <code>{html.escape(s.model_name)}</code>"


def prompt_preview_text(prompt: str, original: str = "", settings=None, remaining: int | None = None, daily_limit: int = 10) -> str:
    shown_original = original.strip() if original and original.strip() else prompt.strip()
    remaining_line = f"\n\nСегодня осталось: {remaining}/{daily_limit}" if remaining is not None else ""
    warning_line = "\n\n⚠️ Проверь перевод." if original and has_unknown_russian(original) else ""
    return (
        "🦝 <b>Черновик готов</b>\n\n"
        "<b>Ты написал:</b>\n"
        f"<code>{html.escape(shown_original[:1400])}</code>\n\n"
        "<b>Промпт для генерации:</b>\n"
        f"<code>{html.escape(prompt[:3000])}</code>"
        + warning_line
        + remaining_line
    )


def presets_text() -> str:
    lines = ["⚡ <b>Быстрые пресеты</b>", "", "▶️ — сразу сгенерировать.", "✍️ — показать промт, чтобы скопировать или дописать.", "", "Доступные идеи:"]
    for preset in QUICK_PRESETS.values():
        lines.append(f"• <b>{preset['title']}</b>")
    return "\n".join(lines)
