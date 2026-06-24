from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton
from config_defaults import MODELS, RESOLUTIONS, SAMPLERS, UC_PRESETS, QUICK_PRESETS, NOISE_SCHEDULES

def rows(buttons, width=2):
    return [buttons[i:i+width] for i in range(0, len(buttons), width)]

def main_menu(channel_url: str = "") -> InlineKeyboardMarkup:
    buttons = [
        InlineKeyboardButton(text="🎨 Новый промт", callback_data="menu:gen"),
        InlineKeyboardButton(text="📎 Img2Img", callback_data="menu:img2img"),
        InlineKeyboardButton(text="⚡ Пресеты", callback_data="menu:presets"),
        InlineKeyboardButton(text="⚙️ Настройки", callback_data="menu:settings"),
        InlineKeyboardButton(text="🔁 Повторить", callback_data="quick:retry"),
        InlineKeyboardButton(text="🕘 История", callback_data="menu:history"),
        InlineKeyboardButton(text="⭐ Избранное", callback_data="menu:favorites"),
        InlineKeyboardButton(text="🩹 Инпейнт", callback_data="menu:inpaint"),
        InlineKeyboardButton(text="🧬 Референс / вайб", callback_data="menu:reference"),
        InlineKeyboardButton(text="🔍 Апскейл", callback_data="menu:upscale"),
        InlineKeyboardButton(text="🌐 NovelAI", url="https://novelai.net/image"),
    ]
    if channel_url:
        buttons.append(InlineKeyboardButton(text="📢 Канал", url=channel_url))
    return InlineKeyboardMarkup(inline_keyboard=rows(buttons, 2))

def pending_prompt_menu(has_image: bool = False, pro: bool = False, compact: bool = False) -> InlineKeyboardMarkup:
    buttons = [
        InlineKeyboardButton(text="✅ Генерировать", callback_data="prompt:confirm"),
        InlineKeyboardButton(text="✏️ Дописать", callback_data="prompt:append"),
        InlineKeyboardButton(text="🔁 Заменить", callback_data="prompt:replace"),
        InlineKeyboardButton(text="🇬🇧 В теги", callback_data="tool:translate"),
    ]
    if pro and not compact:
        buttons.extend([
            InlineKeyboardButton(text="⚙️ Настройки", callback_data="menu:settings"),
            InlineKeyboardButton(text="📐 Размер", callback_data="settings:size"),
            InlineKeyboardButton(text="🧠 Модель", callback_data="settings:model"),
            InlineKeyboardButton(text="🎛 Сэмплер", callback_data="settings:sampler"),
            InlineKeyboardButton(text="👣 Шаги", callback_data="settings:steps"),
            InlineKeyboardButton(text="🧲 CFG / сила промта", callback_data="settings:scale"),
            InlineKeyboardButton(text="🎲 Seed", callback_data="settings:seed"),
            InlineKeyboardButton(text="🚫 Негатив", callback_data="settings:negative"),
            InlineKeyboardButton(text="🧪 UC-пресет", callback_data="settings:uc"),
            InlineKeyboardButton(text="🖼 Кол-во картинок", callback_data="settings:n"),
            InlineKeyboardButton(text="✨ Улучшить промт", callback_data="tool:improve"),
            InlineKeyboardButton(text="🧹 Почистить", callback_data="tool:clean"),
            InlineKeyboardButton(text="📝 Показать исходник", callback_data="prompt:show_original"),
            InlineKeyboardButton(text="🦝 ArtRaccoon vibe", callback_data="tool:raccoon"),
            InlineKeyboardButton(text="👧 Добавить Аэлиту", callback_data="tool:aelita"),
            InlineKeyboardButton(text="📎 Img2Img" + (" ✅" if has_image else ""), callback_data="menu:img2img"),
        ])
    if not pro:
        buttons.append(InlineKeyboardButton(text="⚙️ Настройки", callback_data="menu:settings"))
    buttons.append(InlineKeyboardButton(text="❌ Отмена", callback_data="prompt:cancel"))
    return InlineKeyboardMarkup(inline_keyboard=rows(buttons, 2))

def after_generation_menu() -> InlineKeyboardMarkup:
    buttons = [
        InlineKeyboardButton(text="🔁 Повторить", callback_data="quick:retry"),
        InlineKeyboardButton(text="⭐ В избранное", callback_data="favorite:last"),
        InlineKeyboardButton(text="📝 Показать промт", callback_data="quick:last_prompt"),
        InlineKeyboardButton(text="🌐 NovelAI", url="https://novelai.net/image"),
    ]
    return InlineKeyboardMarkup(inline_keyboard=rows(buttons, 2))

def presets_menu() -> InlineKeyboardMarkup:
    buttons = []
    for key, preset in QUICK_PRESETS.items():
        buttons.append(InlineKeyboardButton(text=f"▶️ {preset['title']}", callback_data=f"preset:gen:{key}"))
        buttons.append(InlineKeyboardButton(text=f"✍️ {preset['title']}", callback_data=f"preset:show:{key}"))
    buttons.append(InlineKeyboardButton(text="⬅️ Назад", callback_data="menu:main"))
    return InlineKeyboardMarkup(inline_keyboard=rows(buttons, 2))

def settings_menu(pro: bool = True) -> InlineKeyboardMarkup:
    buttons = [
        InlineKeyboardButton(text="📐 Размер", callback_data="settings:size"),
        InlineKeyboardButton(text="👣 Шаги", callback_data="settings:steps"),
        InlineKeyboardButton(text="🧲 CFG / сила промта", callback_data="settings:scale"),
        InlineKeyboardButton(text="🎲 Seed", callback_data="settings:seed"),
        InlineKeyboardButton(text="🚫 Негатив", callback_data="settings:negative"),
        InlineKeyboardButton(text="🧠 Модель", callback_data="settings:model"),
    ]
    if pro:
        buttons.extend([
            InlineKeyboardButton(text="🖼 Кол-во картинок", callback_data="settings:n"),
            InlineKeyboardButton(text="🎛 Сэмплер", callback_data="settings:sampler"),
            InlineKeyboardButton(text="🧪 UC-пресет", callback_data="settings:uc"),
            InlineKeyboardButton(text="♻️ CFG rescale", callback_data="settings:cfg"),
            InlineKeyboardButton(text="🌊 Noise", callback_data="settings:noise"),
            InlineKeyboardButton(text="📎 Img2Img сила", callback_data="settings:img2img"),
            InlineKeyboardButton(text="🦝 Режимы", callback_data="settings:modes"),
        ])
    buttons.extend([
        InlineKeyboardButton(text="💎 PRO / Анласы", callback_data="toggle:pro"),
        InlineKeyboardButton(text="♻️ Сброс настроек", callback_data="reset:ask"),
        InlineKeyboardButton(text="⬅️ Назад", callback_data="menu:main"),
    ])
    return InlineKeyboardMarkup(inline_keyboard=rows(buttons, 2))

def confirm_reset_menu(kind: str = "settings") -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="✅ Да, сбросить", callback_data=f"reset:confirm:{kind}")],
        [InlineKeyboardButton(text="❌ Отмена", callback_data="reset:cancel")],
    ])

def model_menu() -> InlineKeyboardMarkup:
    buttons = [InlineKeyboardButton(text=name, callback_data=f"set:model:{name}") for name in MODELS]
    buttons.append(InlineKeyboardButton(text="⬅️ Назад", callback_data="menu:settings"))
    return InlineKeyboardMarkup(inline_keyboard=rows(buttons, 1))

def size_menu() -> InlineKeyboardMarkup:
    buttons = [InlineKeyboardButton(text=name, callback_data=f"set:size:{name}") for name in RESOLUTIONS]
    buttons.append(InlineKeyboardButton(text="🔄 Повернуть", callback_data="set:size:swap"))
    buttons.append(InlineKeyboardButton(text="⬅️ Назад", callback_data="menu:settings"))
    return InlineKeyboardMarkup(inline_keyboard=rows(buttons, 1))

def sampler_menu() -> InlineKeyboardMarkup:
    buttons = [InlineKeyboardButton(text=s, callback_data=f"set:sampler:{s}") for s in SAMPLERS]
    buttons.append(InlineKeyboardButton(text="⬅️ Назад", callback_data="menu:settings"))
    return InlineKeyboardMarkup(inline_keyboard=rows(buttons, 1))

def uc_menu() -> InlineKeyboardMarkup:
    buttons = [InlineKeyboardButton(text=name, callback_data=f"set:uc:{name}") for name in UC_PRESETS]
    buttons.append(InlineKeyboardButton(text="⬅️ Назад", callback_data="menu:settings"))
    return InlineKeyboardMarkup(inline_keyboard=rows(buttons, 1))

def noise_menu() -> InlineKeyboardMarkup:
    buttons = [InlineKeyboardButton(text=name, callback_data=f"set:noise:{name}") for name in NOISE_SCHEDULES]
    buttons.append(InlineKeyboardButton(text="⬅️ Назад", callback_data="menu:settings"))
    return InlineKeyboardMarkup(inline_keyboard=rows(buttons, 1))

def meta_import_menu() -> InlineKeyboardMarkup:
    buttons = [
        InlineKeyboardButton(text="📥 Взять как Base Prompt", callback_data="meta:base"),
        InlineKeyboardButton(text="👤 Взять как Character Prompt", callback_data="meta:character"),
        InlineKeyboardButton(text="🚫 Взять UC/негатив", callback_data="meta:negative"),
        InlineKeyboardButton(text="⚙️ Взять настройки", callback_data="meta:settings"),
        InlineKeyboardButton(text="📦 Взять всё", callback_data="meta:all"),
    ]
    return InlineKeyboardMarkup(inline_keyboard=rows(buttons, 1))

def modes_menu(furry: bool, background: bool, quality: bool) -> InlineKeyboardMarkup:
    buttons = [
        InlineKeyboardButton(text=f"🦊 Furry: {'ON' if furry else 'OFF'}", callback_data="toggle:furry"),
        InlineKeyboardButton(text=f"🌄 Background: {'ON' if background else 'OFF'}", callback_data="toggle:background"),
        InlineKeyboardButton(text=f"✨ Quality tags: {'ON' if quality else 'OFF'}", callback_data="toggle:quality"),
        InlineKeyboardButton(text="⬅️ Назад", callback_data="menu:settings"),
    ]
    return InlineKeyboardMarkup(inline_keyboard=rows(buttons, 1))


def artraccoon_menu() -> InlineKeyboardMarkup:
    buttons = [
        InlineKeyboardButton(text="📜 Базовый промт", callback_data="ar:edit:base"),
        InlineKeyboardButton(text="🚫 Базовый негатив", callback_data="ar:edit:base_uc"),
        InlineKeyboardButton(text="👤 Негатив персонажа", callback_data="ar:edit:char_neg"),
        InlineKeyboardButton(text="🧪 Тест сборки", callback_data="ar:test"),
        InlineKeyboardButton(text="⚙️ Настройки генерации", callback_data="menu:settings"),
        InlineKeyboardButton(text="❌ Выйти из ArtRaccoon режима", callback_data="ar:exit"),
    ]
    return InlineKeyboardMarkup(inline_keyboard=rows(buttons, 1))
