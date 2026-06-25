"""Reusable Telegram text builders."""

import html
from app.services.nai_client import payload_summary
from config_defaults import QUICK_PRESETS
from prompt_tools import has_unknown_russian


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
    remaining_line = f"\n\nСегодня осталось: {remaining}/{daily_limit}" if remaining is not None else ""
    warning_line = "\n\n⚠️ Проверь перевод." if original and has_unknown_russian(original) else ""
    settings_line = "\n\n" + generation_settings_summary(settings) if settings else ""
    if original and original.strip() and original.strip() != prompt.strip():
        return "📝 <b>Промт готов. Запускаем?</b>\n\n<b>Исходник:</b>\n" + f"<code>{html.escape(original[:1400])}</code>\n\n<b>Теговый промт:</b>\n<code>{html.escape(prompt[:3000])}</code>" + warning_line + settings_line + remaining_line
    return "📝 <b>Промт готов. Запускаем?</b>\n\n" + f"<code>{html.escape(prompt[:3000])}</code>" + warning_line + settings_line + remaining_line


def presets_text() -> str:
    lines = ["⚡ <b>Быстрые пресеты</b>", "", "▶️ — сразу сгенерировать.", "✍️ — показать промт, чтобы скопировать или дописать.", "", "Доступные идеи:"]
    for preset in QUICK_PRESETS.values():
        lines.append(f"• <b>{preset['title']}</b>")
    return "\n".join(lines)
