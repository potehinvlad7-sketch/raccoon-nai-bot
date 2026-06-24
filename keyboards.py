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
        InlineKeyboardButton(text="🩹 Inpaint", callback_data="menu:inpaint"),
        InlineKeyboardButton(text="🧬 Vibe / Reference", callback_data="menu:reference"),
        InlineKeyboardButton(text="🔍 Upscale / Enhance", callback_data="menu:upscale"),
        InlineKeyboardButton(text="🌐 NovelAI website", url="https://novelai.net/image"),
    ]
    if channel_url:
        buttons.append(InlineKeyboardButton(text="📢 Channel", url=channel_url))
    return InlineKeyboardMarkup(inline_keyboard=rows(buttons, 2))

def pending_prompt_menu(has_image: bool = False) -> InlineKeyboardMarkup:
    buttons = [
        InlineKeyboardButton(text="✅ Generate", callback_data="prompt:confirm"),
        InlineKeyboardButton(text="⚙️ Settings", callback_data="menu:settings"),
        InlineKeyboardButton(text="📐 Size", callback_data="settings:size"),
        InlineKeyboardButton(text="🧠 Model", callback_data="settings:model"),
        InlineKeyboardButton(text="🎛 Sampler", callback_data="settings:sampler"),
        InlineKeyboardButton(text="👣 Steps", callback_data="settings:steps"),
        InlineKeyboardButton(text="🧲 Scale / CFG", callback_data="settings:scale"),
        InlineKeyboardButton(text="🎲 Seed", callback_data="settings:seed"),
        InlineKeyboardButton(text="🚫 Negative prompt", callback_data="settings:negative"),
        InlineKeyboardButton(text="🧪 UC preset", callback_data="settings:uc"),
        InlineKeyboardButton(text="🖼 Samples count", callback_data="settings:n"),
        InlineKeyboardButton(text="✨ Improve prompt", callback_data="tool:improve"),
        InlineKeyboardButton(text="🧹 Clean prompt", callback_data="tool:clean"),
        InlineKeyboardButton(text="🇬🇧 Translate to English", callback_data="tool:translate"),
        InlineKeyboardButton(text="🦝 Add ArtRaccoon vibe", callback_data="tool:raccoon"),
        InlineKeyboardButton(text="👧 Add Aelita character", callback_data="tool:aelita"),
        InlineKeyboardButton(text="📎 Img2Img" + (" ✅" if has_image else ""), callback_data="menu:img2img"),
        InlineKeyboardButton(text="❌ Cancel", callback_data="prompt:cancel"),
    ]
    return InlineKeyboardMarkup(inline_keyboard=rows(buttons, 2))

def after_generation_menu() -> InlineKeyboardMarkup:
    buttons = [
        InlineKeyboardButton(text="🔁 Retry", callback_data="quick:retry"),
        InlineKeyboardButton(text="⭐ Favorite", callback_data="favorite:last"),
        InlineKeyboardButton(text="📝 Show prompt", callback_data="quick:last_prompt"),
        InlineKeyboardButton(text="🌐 NovelAI website", url="https://novelai.net/image"),
    ]
    return InlineKeyboardMarkup(inline_keyboard=rows(buttons, 2))

def presets_menu() -> InlineKeyboardMarkup:
    buttons = []
    for key, preset in QUICK_PRESETS.items():
        buttons.append(InlineKeyboardButton(text=f"▶️ {preset['title']}", callback_data=f"preset:gen:{key}"))
        buttons.append(InlineKeyboardButton(text=f"✍️ {preset['title']}", callback_data=f"preset:show:{key}"))
    buttons.append(InlineKeyboardButton(text="⬅️ Назад", callback_data="menu:main"))
    return InlineKeyboardMarkup(inline_keyboard=rows(buttons, 2))

def settings_menu() -> InlineKeyboardMarkup:
    buttons = [
        InlineKeyboardButton(text="🧠 Модель", callback_data="settings:model"),
        InlineKeyboardButton(text="🖼 Размер", callback_data="settings:size"),
        InlineKeyboardButton(text="🔢 Кол-во", callback_data="settings:n"),
        InlineKeyboardButton(text="🪜 Steps", callback_data="settings:steps"),
        InlineKeyboardButton(text="🧲 Guidance", callback_data="settings:scale"),
        InlineKeyboardButton(text="🎲 Seed", callback_data="settings:seed"),
        InlineKeyboardButton(text="🧬 Sampler", callback_data="settings:sampler"),
        InlineKeyboardButton(text="🚫 Negative", callback_data="settings:negative"),
        InlineKeyboardButton(text="🧹 UC preset", callback_data="settings:uc"),
        InlineKeyboardButton(text="♻️ CFG rescale", callback_data="settings:cfg"),
        InlineKeyboardButton(text="🌊 Noise", callback_data="settings:noise"),
        InlineKeyboardButton(text="📎 Img2Img сила", callback_data="settings:img2img"),
        InlineKeyboardButton(text="🦝 Режимы", callback_data="settings:modes"),
        InlineKeyboardButton(text="⬅️ Назад", callback_data="menu:main"),
    ]
    return InlineKeyboardMarkup(inline_keyboard=rows(buttons, 2))

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

def numeric_menu(field: str, values: list[str]) -> InlineKeyboardMarkup:
    buttons = [InlineKeyboardButton(text=v, callback_data=f"set:{field}:{v}") for v in values]
    buttons.append(InlineKeyboardButton(text="⬅️ Назад", callback_data="menu:settings"))
    return InlineKeyboardMarkup(inline_keyboard=rows(buttons, 3))

def modes_menu(furry: bool, background: bool, quality: bool) -> InlineKeyboardMarkup:
    buttons = [
        InlineKeyboardButton(text=f"🦊 Furry: {'ON' if furry else 'OFF'}", callback_data="toggle:furry"),
        InlineKeyboardButton(text=f"🌄 Background: {'ON' if background else 'OFF'}", callback_data="toggle:background"),
        InlineKeyboardButton(text=f"✨ Quality tags: {'ON' if quality else 'OFF'}", callback_data="toggle:quality"),
        InlineKeyboardButton(text="⬅️ Назад", callback_data="menu:settings"),
    ]
    return InlineKeyboardMarkup(inline_keyboard=rows(buttons, 1))
