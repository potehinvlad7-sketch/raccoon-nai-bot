import json
from pathlib import Path
from config_defaults import UserSettings

DATA_DIR = Path("data")
USERS_FILE = DATA_DIR / "users.json"

def _ensure() -> None:
    DATA_DIR.mkdir(exist_ok=True)
    if not USERS_FILE.exists():
        USERS_FILE.write_text("{}", encoding="utf-8")

def load_all() -> dict:
    _ensure()
    try:
        return json.loads(USERS_FILE.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return {}

def save_all(data: dict) -> None:
    _ensure()
    tmp = USERS_FILE.with_suffix(".tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    tmp.replace(USERS_FILE)

def get_settings(user_id: int) -> UserSettings:
    data = load_all()
    raw = data.get(str(user_id), {})
    defaults = UserSettings().to_dict()
    defaults.update({k: v for k, v in raw.items() if k in defaults})
    return UserSettings(**defaults)

def save_settings(user_id: int, settings: UserSettings) -> None:
    data = load_all()
    user = data.setdefault(str(user_id), {})
    user.update(settings.to_dict())
    save_all(data)

def patch_settings(user_id: int, **kwargs) -> UserSettings:
    settings = get_settings(user_id)
    for key, value in kwargs.items():
        if hasattr(settings, key):
            setattr(settings, key, value)
    save_settings(user_id, settings)
    return settings


def add_history(user_id: int, item: dict, limit: int = 20) -> None:
    data = load_all()
    key = str(user_id)
    user = data.setdefault(key, get_settings(user_id).to_dict())
    history = user.setdefault("history", [])
    history.insert(0, item)
    del history[limit:]
    save_all(data)

def get_history(user_id: int) -> list[dict]:
    data = load_all()
    return list(data.get(str(user_id), {}).get("history", []))

def add_favorite(user_id: int, item: dict, limit: int = 50) -> None:
    data = load_all()
    key = str(user_id)
    user = data.setdefault(key, get_settings(user_id).to_dict())
    favorites = user.setdefault("favorites", [])
    favorites.insert(0, item)
    del favorites[limit:]
    save_all(data)

def get_favorites(user_id: int) -> list[dict]:
    data = load_all()
    return list(data.get(str(user_id), {}).get("favorites", []))

def delete_favorite(user_id: int, index: int) -> bool:
    data = load_all()
    user = data.get(str(user_id), {})
    favorites = user.get("favorites", [])
    if not isinstance(favorites, list) or index < 0 or index >= len(favorites):
        return False
    del favorites[index]
    save_all(data)
    return True


def set_last_metadata(user_id: int, metadata: dict) -> None:
    data = load_all()
    key = str(user_id)
    user = data.setdefault(key, get_settings(user_id).to_dict())
    user["last_metadata"] = metadata
    save_all(data)

def get_last_metadata(user_id: int) -> dict:
    data = load_all()
    meta = data.get(str(user_id), {}).get("last_metadata", {})
    return meta if isinstance(meta, dict) else {}


def set_last_payload(user_id: int, payload: dict) -> None:
    data = load_all()
    key = str(user_id)
    user = data.setdefault(key, get_settings(user_id).to_dict())
    user["last_nai_payload"] = payload
    save_all(data)


def get_last_payload(user_id: int) -> dict:
    data = load_all()
    payload = data.get(str(user_id), {}).get("last_nai_payload", {})
    return payload if isinstance(payload, dict) else {}
