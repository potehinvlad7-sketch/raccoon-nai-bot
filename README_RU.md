# NovelAI Telegram Bot — fullstarter

Стартовый каркас личного Telegram-бота для NovelAI Image.

## Быстрый запуск на Ubuntu

```bash
cd ~/bot
python3 -m venv venv
source venv/bin/activate
pip install -r requirements.txt
cp .env.example .env
nano .env
python bot.py
```

## Команды

- `/start` — главное меню
- `/gen prompt` — генерация
- `/settings` — настройки
- `/prompt` — посмотреть текущий промт
- `/raw` — показать raw overrides без секретов
- `/cancel` — отменить ввод промта или токена
- `/help` — помощь

## Админ-панель и NovelAI ключ

- Кнопка `🛠 Админ-панель` видна только Telegram ID из `ADMIN_IDS`.
- Админы могут установить, проверить, удалить и посмотреть статус глобального NovelAI Persistent API Token.
- Токен сохраняется в `data/secrets.json`; каталог `data/` игнорируется Git.
- Персональные токены пользователей не используются. Все генерации берут глобальный токен.
- Приоритет токена: `data/secrets.json` → `NAI_TOKEN` из `.env` → отсутствие ключа.
- В интерфейсе показывается только источник/статус ключа, само значение токена не выводится.

## Важно

NovelAI может менять API. Каркас сделан так, чтобы параметры можно было править в `nai_client.py` и `config_defaults.py`, не ломая меню.
