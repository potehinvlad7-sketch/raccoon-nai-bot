from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton
from config_defaults import MODELS, RESOLUTIONS, SAMPLERS, UC_PRESETS

def rows(buttons, width=2):
    return [buttons[i:i+width] for i in range(0, len(buttons), width)]

def main_menu(is_admin: bool = False) -> InlineKeyboardMarkup:
    buttons = [
        InlineKeyboardButton(text="🎨 Генерация", callback_data="menu:gen"),
        InlineKeyboardButton(text="⚙️ Настройки", callback_data="menu:settings"),
        InlineKeyboardButton(text="🧪 Пресеты", callback_data="menu:presets"),
        InlineKeyboardButton(text="📎 Img2Img", callback_data="menu:img2img"),
        InlineKeyboardButton(text="❔ Помощь", callback_data="menu:help"),
    ]
    if is_admin:
        buttons.append(InlineKeyboardButton(text="🛠 Админ-панель", callback_data="admin:panel"))
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


def admin_menu() -> InlineKeyboardMarkup:
    buttons = [
        InlineKeyboardButton(text="🔑 Установить NovelAI ключ", callback_data="admin:set_token"),
        InlineKeyboardButton(text="🧪 Проверить ключ", callback_data="admin:test_token"),
        InlineKeyboardButton(text="🗑 Удалить ключ", callback_data="admin:delete_token"),
        InlineKeyboardButton(text="ℹ️ Статус ключа", callback_data="admin:token_status"),
        InlineKeyboardButton(text="⬅️ Назад", callback_data="menu:main"),
    ]
    return InlineKeyboardMarkup(inline_keyboard=rows(buttons, 1))
