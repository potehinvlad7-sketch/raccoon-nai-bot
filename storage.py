import json
import os
from pathlib import Path
from config_defaults import UserSettings

DATA_DIR = Path("data")
USERS_FILE = DATA_DIR / "users.json"
SECRETS_FILE = DATA_DIR / "secrets.json"
NAI_TOKEN_KEY = "nai_token"

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
    defaults.update(raw)
    return UserSettings(**defaults)

def save_settings(user_id: int, settings: UserSettings) -> None:
    data = load_all()
    data[str(user_id)] = settings.to_dict()
    save_all(data)

def patch_settings(user_id: int, **kwargs) -> UserSettings:
    settings = get_settings(user_id)
    for key, value in kwargs.items():
        if hasattr(settings, key):
            setattr(settings, key, value)
    save_settings(user_id, settings)
    return settings


def _load_secrets() -> dict:
    DATA_DIR.mkdir(exist_ok=True)
    if not SECRETS_FILE.exists():
        return {}
    try:
        return json.loads(SECRETS_FILE.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return {}

def _save_secrets(data: dict) -> None:
    DATA_DIR.mkdir(exist_ok=True)
    tmp = SECRETS_FILE.with_suffix(".tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    tmp.replace(SECRETS_FILE)

def get_global_nai_token() -> str:
    saved_token = str(_load_secrets().get(NAI_TOKEN_KEY, "")).strip()
    if saved_token:
        return saved_token
    return os.getenv("NAI_TOKEN", "").strip()

def set_global_nai_token(token: str) -> None:
    data = _load_secrets()
    data[NAI_TOKEN_KEY] = token.strip()
    _save_secrets(data)

def delete_global_nai_token() -> None:
    data = _load_secrets()
    data.pop(NAI_TOKEN_KEY, None)
    _save_secrets(data)

def has_global_nai_token() -> bool:
    return bool(get_global_nai_token())

def get_token_source() -> str:
    if str(_load_secrets().get(NAI_TOKEN_KEY, "")).strip():
        return "admin_saved"
    if os.getenv("NAI_TOKEN", "").strip():
        return "env"
    return "missing"
