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
- `/raw` — показать raw overrides (только админы)
- `/characters` — открыть админскую панель Character+
- `/char` — короткая команда для Character+
- `/help` — помощь

## Character+

Character+ — админская функция для добавления до 6 дополнительных персонажей с отдельными `prompt`, `uc` и `position`. Для моделей V4/V4.5 бот отправляет этих персонажей отдельными `char_captions` в `v4_prompt.caption.char_captions` и `v4_negative_prompt.caption.char_captions`. Для старых моделей, принудительного concat-режима или fallback-повтора после ошибки NovelAI персонажи объединяются с обычным prompt/negative prompt. Обычным пользователям команды `/characters` и `/char` скрыты и отвечают как неизвестные.

## Важно

NovelAI может менять API. Каркас сделан так, чтобы параметры можно было править в `nai_client.py` и `config_defaults.py`, не ломая меню.

## Локальный FastAPI сервер

Для будущей интеграции с ChatGPT Actions можно запустить локальный API-сервер:

```bash
uvicorn app.api_server:app --host 127.0.0.1 --port 8090
```

Сервер сейчас предназначен только для локального использования. Не публикуйте его в интернет и не открывайте публичный доступ, пока интеграция и безопасность не будут подготовлены отдельно.
