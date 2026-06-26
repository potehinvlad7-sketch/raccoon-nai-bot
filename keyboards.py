from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton
from config_defaults import MODELS, RESOLUTIONS, SAMPLERS, UC_PRESETS, QUICK_PRESETS, NOISE_SCHEDULES

def rows(buttons, width=2):
    return [buttons[i:i+width] for i in range(0, len(buttons), width)]

def main_menu_button() -> InlineKeyboardButton:
    return InlineKeyboardButton(text="🏠 Главное меню", callback_data="menu:main")

def main_menu(channel_url: str = "") -> InlineKeyboardMarkup:
    buttons = [
        InlineKeyboardButton(text="🎨 Новый промпт", callback_data="menu:gen"),
        InlineKeyboardButton(text="💎 Купить генерации", callback_data="paid:buy"),
        InlineKeyboardButton(text="❓ Помощь", callback_data="menu:howto"),
    ]
    if channel_url:
        buttons.append(InlineKeyboardButton(text="📢 Канал", url=channel_url))
    return InlineKeyboardMarkup(inline_keyboard=rows(buttons, 2))

def pending_prompt_menu(has_image: bool = False, pro: bool = False, compact: bool = False, vibe_enabled: bool = False, vibe_available: bool = False) -> InlineKeyboardMarkup:
    buttons = [
        InlineKeyboardButton(text="✅ Генерировать", callback_data="prompt:confirm"),
        InlineKeyboardButton(text="✏️ Редактировать", callback_data="prompt:replace"),
        InlineKeyboardButton(text="🧹 Очистить", callback_data="prompt:clear"),
        InlineKeyboardButton(text=f"🦝 ArtRaccoon vibe: {'ON' if vibe_enabled and vibe_available else 'OFF'}", callback_data="prompt:ar_vibe"),
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
            InlineKeyboardButton(text="🦝 Добавить Аэлиту", callback_data="tool:aelita"),
            InlineKeyboardButton(text="📎 Img2Img" + (" ✅" if has_image else ""), callback_data="menu:img2img"),
        ])
    buttons.append(InlineKeyboardButton(text="❌ Отмена", callback_data="prompt:cancel"))
    if pro:
        buttons.append(InlineKeyboardButton(text="⚙️ Расширенные настройки", callback_data="paid:settings"))
    buttons.append(main_menu_button())
    return InlineKeyboardMarkup(inline_keyboard=rows(buttons, 2))

def generation_item_menu(kind: str, index: int) -> InlineKeyboardMarkup:
    buttons = [
        InlineKeyboardButton(text="🔁 Повторить", callback_data=f"{kind}:retry:{index}"),
        InlineKeyboardButton(text="📝 Показать промт", callback_data=f"{kind}:prompt:{index}"),
    ]
    if kind == "history":
        buttons.insert(1, InlineKeyboardButton(text="⭐ В избранное", callback_data=f"history:fav:{index}"))
    if kind == "fav":
        buttons.insert(1, InlineKeyboardButton(text="🗑 Удалить", callback_data=f"fav:del:{index}"))
    buttons.append(main_menu_button())
    return InlineKeyboardMarkup(inline_keyboard=rows(buttons, 2))

def after_generation_menu() -> InlineKeyboardMarkup:
    buttons = [
        InlineKeyboardButton(text="🔁 Повторить", callback_data="quick:retry"),
        InlineKeyboardButton(text="✏️ Изменить промпт", callback_data="quick:edit_prompt"),
        InlineKeyboardButton(text="🌊 Свайп / вариация", callback_data="paid:variation"),
        InlineKeyboardButton(text="⬆️ Up", callback_data="paid:upscale"),
        main_menu_button(),
    ]
    return InlineKeyboardMarkup(inline_keyboard=rows(buttons, 2))

def presets_menu() -> InlineKeyboardMarkup:
    buttons = []
    for key, preset in QUICK_PRESETS.items():
        buttons.append(InlineKeyboardButton(text=f"▶️ {preset['title']}", callback_data=f"preset:gen:{key}"))
        buttons.append(InlineKeyboardButton(text=f"✍️ {preset['title']}", callback_data=f"preset:show:{key}"))
    buttons.append(InlineKeyboardButton(text="⬅️ Назад", callback_data="menu:main"))
    buttons.append(main_menu_button())
    return InlineKeyboardMarkup(inline_keyboard=rows(buttons, 2))

def settings_menu(pro: bool = True, show_pro_button: bool = True) -> InlineKeyboardMarkup:
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
    if show_pro_button:
        buttons.append(InlineKeyboardButton(text="💎 PRO / Анласы", callback_data="toggle:pro"))
    buttons.extend([
        InlineKeyboardButton(text="♻️ Сброс настроек", callback_data="reset:ask"),
        InlineKeyboardButton(text="⬅️ Назад", callback_data="menu:main"),
        main_menu_button(),
    ])
    return InlineKeyboardMarkup(inline_keyboard=rows(buttons, 2))

def confirm_reset_menu(kind: str = "settings") -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="✅ Да, сбросить", callback_data=f"reset:confirm:{kind}")],
        [InlineKeyboardButton(text="❌ Отмена", callback_data="reset:cancel")],
        [main_menu_button()],
    ])

def model_menu() -> InlineKeyboardMarkup:
    buttons = [InlineKeyboardButton(text=name, callback_data=f"set:model:{name}") for name in MODELS]
    buttons.append(InlineKeyboardButton(text="⬅️ Назад", callback_data="menu:settings"))
    buttons.append(main_menu_button())
    return InlineKeyboardMarkup(inline_keyboard=rows(buttons, 1))

def size_menu() -> InlineKeyboardMarkup:
    buttons = [InlineKeyboardButton(text=name, callback_data=f"set:size:{name}") for name in RESOLUTIONS]
    buttons.append(InlineKeyboardButton(text="🔄 Повернуть", callback_data="set:size:swap"))
    buttons.append(InlineKeyboardButton(text="⬅️ Назад", callback_data="menu:settings"))
    buttons.append(main_menu_button())
    return InlineKeyboardMarkup(inline_keyboard=rows(buttons, 1))

def sampler_menu() -> InlineKeyboardMarkup:
    buttons = [InlineKeyboardButton(text=s, callback_data=f"set:sampler:{s}") for s in SAMPLERS]
    buttons.append(InlineKeyboardButton(text="⬅️ Назад", callback_data="menu:settings"))
    buttons.append(main_menu_button())
    return InlineKeyboardMarkup(inline_keyboard=rows(buttons, 1))

def uc_menu() -> InlineKeyboardMarkup:
    buttons = [InlineKeyboardButton(text=name, callback_data=f"set:uc:{name}") for name in UC_PRESETS]
    buttons.append(InlineKeyboardButton(text="⬅️ Назад", callback_data="menu:settings"))
    buttons.append(main_menu_button())
    return InlineKeyboardMarkup(inline_keyboard=rows(buttons, 1))

def noise_menu() -> InlineKeyboardMarkup:
    buttons = [InlineKeyboardButton(text=name, callback_data=f"set:noise:{name}") for name in NOISE_SCHEDULES]
    buttons.append(InlineKeyboardButton(text="⬅️ Назад", callback_data="menu:settings"))
    buttons.append(main_menu_button())
    return InlineKeyboardMarkup(inline_keyboard=rows(buttons, 1))

def seed_menu() -> InlineKeyboardMarkup:
    buttons = [
        InlineKeyboardButton(text="🎲 random", callback_data="set:seed:-1"),
        InlineKeyboardButton(text="✍️ ввести seed", callback_data="settings_input:seed"),
        InlineKeyboardButton(text="⬅️ Назад", callback_data="menu:settings"),
        main_menu_button(),
    ]
    return InlineKeyboardMarkup(inline_keyboard=rows(buttons, 1))

def samples_menu() -> InlineKeyboardMarkup:
    buttons = [InlineKeyboardButton(text=str(n), callback_data=f"set:n:{n}") for n in (1, 2, 3, 4)]
    buttons.append(InlineKeyboardButton(text="⬅️ Назад", callback_data="menu:settings"))
    buttons.append(main_menu_button())
    return InlineKeyboardMarkup(inline_keyboard=rows(buttons, 2))

def meta_import_menu() -> InlineKeyboardMarkup:
    buttons = [
        InlineKeyboardButton(text="📥 Взять как Base Prompt", callback_data="meta:base"),
        InlineKeyboardButton(text="👤 Взять как Character Prompt", callback_data="meta:character"),
        InlineKeyboardButton(text="🚫 Взять UC/негатив", callback_data="meta:negative"),
        InlineKeyboardButton(text="⚙️ Применить все настройки сайта", callback_data="meta:settings"),
        InlineKeyboardButton(text="📋 Показать настройки metadata", callback_data="meta:show_settings"),
        InlineKeyboardButton(text="📦 Взять всё", callback_data="meta:all"),
        main_menu_button(),
    ]
    return InlineKeyboardMarkup(inline_keyboard=rows(buttons, 1))

def modes_menu(furry: bool, background: bool, quality: bool, variety_plus: bool = True) -> InlineKeyboardMarkup:
    buttons = [
        InlineKeyboardButton(text=f"🦊 Furry: {'ON' if furry else 'OFF'}", callback_data="toggle:furry"),
        InlineKeyboardButton(text=f"🌄 Background: {'ON' if background else 'OFF'}", callback_data="toggle:background"),
        InlineKeyboardButton(text=f"✨ Quality tags: {'ON' if quality else 'OFF'}", callback_data="toggle:quality"),
        InlineKeyboardButton(text=f"🎨 Variety+: {'ON' if variety_plus else 'OFF'}", callback_data="toggle:variety"),
        InlineKeyboardButton(text="⬅️ Назад", callback_data="menu:settings"),
        main_menu_button(),
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
        main_menu_button(),
    ]
    return InlineKeyboardMarkup(inline_keyboard=rows(buttons, 1))


def moderation_dictionary_menu(token: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="✅ Review", callback_data=f"dict_review:{token}"), InlineKeyboardButton(text="❌ Reject", callback_data=f"dict_reject:{token}")],
        [InlineKeyboardButton(text="📚 Dictionary", callback_data="dict:menu")],
    ])

def dictionary_menu() -> InlineKeyboardMarkup:
    buttons = [
        InlineKeyboardButton(text="📚 Statistics", callback_data="dict:stats"),
        InlineKeyboardButton(text="🕓 Pending", callback_data="dict:pending"),
        InlineKeyboardButton(text="➕ Add manually", callback_data="dict:add"),
        InlineKeyboardButton(text="📤 Export", callback_data="dict:export"),
        InlineKeyboardButton(text="📥 Import", callback_data="dict:import"),
        InlineKeyboardButton(text="🧹 Cleanup rejected", callback_data="dict:cleanup"),
        main_menu_button(),
    ]
    return InlineKeyboardMarkup(inline_keyboard=rows(buttons, 2))

def dictionary_pending_menu(tags: list[str]) -> InlineKeyboardMarkup:
    buttons = [InlineKeyboardButton(text=tag[:60], callback_data=f"dict_one:{i}") for i, tag in enumerate(tags[:40])]
    buttons.append(InlineKeyboardButton(text="❌ Reject all", callback_data="dict:reject_pending"))
    buttons.append(InlineKeyboardButton(text="⬅️ Dictionary", callback_data="dict:menu"))
    return InlineKeyboardMarkup(inline_keyboard=rows(buttons, 1))

def admin_panel_menu() -> InlineKeyboardMarkup:
    buttons = [
        InlineKeyboardButton(text="📊 Статистика", callback_data="admin:stats"),
        InlineKeyboardButton(text="⚙️ Дефолты обычного режима", callback_data="admin:basic_defaults"),
        InlineKeyboardButton(text="🦝 ArtRaccoon Vibe", callback_data="admin:ar_vibe"),
        InlineKeyboardButton(text="🧪 NovelAI debug", callback_data="admin:nai_debug"),
        InlineKeyboardButton(text="📚 Словарь", callback_data="admin:dict"),
        InlineKeyboardButton(text="💎 Покупки / генерации", callback_data="admin:soon:purchases"),
        InlineKeyboardButton(text="👥 Пользователи", callback_data="admin:soon:users"),
        InlineKeyboardButton(text="📢 Рассылка", callback_data="admin:soon:broadcast"),
        main_menu_button(),
    ]
    return InlineKeyboardMarkup(inline_keyboard=rows(buttons, 1))
