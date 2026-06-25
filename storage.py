import json
import os
import threading
from pathlib import Path
from config_defaults import UserSettings

DATA_DIR = Path("data")
USERS_FILE = DATA_DIR / "users.json"
_STORAGE_LOCK = threading.RLock()


def _ensure() -> None:
    DATA_DIR.mkdir(exist_ok=True)
    if not USERS_FILE.exists():
        USERS_FILE.write_text("{}", encoding="utf-8")


def _load_all_unlocked() -> dict:
    _ensure()
    try:
        data = json.loads(USERS_FILE.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return {}
    return data if isinstance(data, dict) else {}


def _save_all_unlocked(data: dict) -> None:
    _ensure()
    tmp = USERS_FILE.with_suffix(".tmp")
    with tmp.open("w", encoding="utf-8") as fh:
        json.dump(data, fh, ensure_ascii=False, indent=2)
        fh.write("\n")
        fh.flush()
        os.fsync(fh.fileno())
    tmp.replace(USERS_FILE)


def _default_user(raw: dict | None = None) -> dict:
    defaults = UserSettings().to_dict()
    if isinstance(raw, dict):
        defaults.update({k: v for k, v in raw.items() if k in defaults})
        for key in ("history", "favorites", "last_metadata", "last_nai_payload"):
            if key in raw:
                defaults[key] = raw[key]
    return defaults


def load_all() -> dict:
    with _STORAGE_LOCK:
        return _load_all_unlocked()


def save_all(data: dict) -> None:
    with _STORAGE_LOCK:
        _save_all_unlocked(data)


def get_settings(user_id: int) -> UserSettings:
    with _STORAGE_LOCK:
        raw = _load_all_unlocked().get(str(user_id), {})
        defaults = UserSettings().to_dict()
        if isinstance(raw, dict):
            defaults.update({k: v for k, v in raw.items() if k in defaults})
        return UserSettings(**defaults)


def save_settings(user_id: int, settings: UserSettings) -> None:
    with _STORAGE_LOCK:
        data = _load_all_unlocked()
        user = data.setdefault(str(user_id), {})
        user.update(settings.to_dict())
        _save_all_unlocked(data)


def patch_settings(user_id: int, **kwargs) -> UserSettings:
    with _STORAGE_LOCK:
        data = _load_all_unlocked()
        key = str(user_id)
        raw = data.get(key, {})
        user = _default_user(raw if isinstance(raw, dict) else {})
        settings_data = {k: v for k, v in user.items() if k in UserSettings().to_dict()}
        settings = UserSettings(**settings_data)
        for field, value in kwargs.items():
            if hasattr(settings, field):
                setattr(settings, field, value)
        user.update(settings.to_dict())
        data[key] = user
        _save_all_unlocked(data)
        return settings


def add_history(user_id: int, item: dict, limit: int = 20) -> None:
    with _STORAGE_LOCK:
        data = _load_all_unlocked()
        user = data.setdefault(str(user_id), _default_user())
        history = user.setdefault("history", [])
        history.insert(0, item)
        del history[limit:]
        _save_all_unlocked(data)


def get_history(user_id: int) -> list[dict]:
    with _STORAGE_LOCK:
        history = _load_all_unlocked().get(str(user_id), {}).get("history", [])
        return list(history) if isinstance(history, list) else []


def add_favorite(user_id: int, item: dict, limit: int = 50) -> None:
    with _STORAGE_LOCK:
        data = _load_all_unlocked()
        user = data.setdefault(str(user_id), _default_user())
        favorites = user.setdefault("favorites", [])
        favorites.insert(0, item)
        del favorites[limit:]
        _save_all_unlocked(data)


def get_favorites(user_id: int) -> list[dict]:
    with _STORAGE_LOCK:
        favorites = _load_all_unlocked().get(str(user_id), {}).get("favorites", [])
        return list(favorites) if isinstance(favorites, list) else []


def delete_favorite(user_id: int, index: int) -> bool:
    with _STORAGE_LOCK:
        data = _load_all_unlocked()
        user = data.get(str(user_id), {})
        favorites = user.get("favorites", []) if isinstance(user, dict) else []
        if not isinstance(favorites, list) or index < 0 or index >= len(favorites):
            return False
        del favorites[index]
        _save_all_unlocked(data)
        return True


def set_last_metadata(user_id: int, metadata: dict) -> None:
    with _STORAGE_LOCK:
        data = _load_all_unlocked()
        user = data.setdefault(str(user_id), _default_user())
        user["last_metadata"] = metadata
        _save_all_unlocked(data)


def get_last_metadata(user_id: int) -> dict:
    with _STORAGE_LOCK:
        meta = _load_all_unlocked().get(str(user_id), {}).get("last_metadata", {})
        return meta if isinstance(meta, dict) else {}


def set_last_payload(user_id: int, payload: dict) -> None:
    with _STORAGE_LOCK:
        data = _load_all_unlocked()
        user = data.setdefault(str(user_id), _default_user())
        user["last_nai_payload"] = payload
        _save_all_unlocked(data)


def get_last_payload(user_id: int) -> dict:
    with _STORAGE_LOCK:
        payload = _load_all_unlocked().get(str(user_id), {}).get("last_nai_payload", {})
        return payload if isinstance(payload, dict) else {}
