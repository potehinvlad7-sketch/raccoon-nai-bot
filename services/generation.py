"""Generation settings, quota and safe file helpers."""

import re
from datetime import datetime, timezone
from pathlib import Path

from config_defaults import MODELS, UserSettings
from storage import get_settings, patch_settings

SAFE_RESOLUTIONS = {(512, 768), (768, 1344), (832, 1216), (1024, 1024), (1216, 832)}
DAILY_GENERATION_LIMIT = 10
NON_ADMIN_COOLDOWN_SECONDS = 60
GENERATION_TIMEOUT_SECONDS = 180
TMP_DIR = Path("data/tmp_images")
GENERATED_DIR = Path("data/generated")
TMP_DIR.mkdir(parents=True, exist_ok=True)
GENERATED_DIR.mkdir(parents=True, exist_ok=True)
# FIXME: add safe generated-image cleanup by age and total storage size when retention policy is defined.


def assemble_ar_prompt(s, character_prompt: str) -> str:
    return ", ".join(part.strip() for part in [s.artraccoon_base_prompt, character_prompt] if part.strip())


def ar_payload_mode(s, nai_model: str = "") -> str:
    if s.artraccoon_force_concat:
        return "fallback concat (forced)"
    model = nai_model or MODELS.get(s.model_name, "")
    return "Character Payload for v4/v4.5" if model.startswith(("nai-diffusion-4", "nai-diffusion-4-5")) else "fallback concat"


def safe_generation_defaults() -> dict:
    defaults = UserSettings()
    return {"width": defaults.width, "height": defaults.height, "steps": defaults.steps, "scale": defaults.scale, "seed": defaults.seed, "negative_prompt": defaults.negative_prompt, "model_name": defaults.model_name, "sampler": defaults.sampler, "n_samples": 1, "uc_preset": defaults.uc_preset, "cfg_rescale": defaults.cfg_rescale, "noise_schedule": defaults.noise_schedule, "variety_plus": defaults.variety_plus, "img2img_strength": defaults.img2img_strength, "img2img_noise": defaults.img2img_noise, "pro_mode": False, "nai_site_mode": False}


def artraccoon_prompt_defaults() -> dict:
    return {"artraccoon_base_prompt": "", "artraccoon_base_uc": "", "artraccoon_character_prompt": "", "artraccoon_character_uc": "", "artraccoon_character_negative": "", "artraccoon_character_position": ""}


def today_key() -> str:
    return datetime.now(timezone.utc).date().isoformat()


def daily_count_for(s) -> int:
    return int(s.daily_generation_count or 0) if s.daily_generation_date == today_key() else 0


def remaining_generations(user_id: int, admin_ids: list[int]) -> int | None:
    if user_id in admin_ids:
        return None
    return max(0, DAILY_GENERATION_LIMIT - daily_count_for(get_settings(user_id)))


def mark_generation_started(user_id: int, admin_ids: list[int]) -> None:
    s = get_settings(user_id)
    updates = {"last_generation_started_at": datetime.now(timezone.utc).isoformat()}
    if user_id not in admin_ids:
        updates.update({"daily_generation_date": today_key(), "daily_generation_count": daily_count_for(s) + 1})
    patch_settings(user_id, **updates)


def cooldown_remaining(user_id: int, admin_ids: list[int]) -> int:
    if user_id in admin_ids:
        return 0
    raw = get_settings(user_id).last_generation_started_at
    if not raw:
        return 0
    try:
        started = datetime.fromisoformat(raw)
    except ValueError:
        return 0
    elapsed = (datetime.now(timezone.utc) - started).total_seconds()
    return max(0, int(NON_ADMIN_COOLDOWN_SECONDS - elapsed))


def apply_anlas_safe_defaults(user_id: int, admin_ids: list[int]):
    s = get_settings(user_id)
    if s.pro_mode and user_id in admin_ids:
        return s
    updates = {}
    if s.n_samples != 1:
        updates["n_samples"] = 1
    if s.steps > 28:
        updates["steps"] = 28
    if (s.width, s.height) not in SAFE_RESOLUTIONS:
        updates.update({"width": 832, "height": 1216})
    return patch_settings(user_id, **updates) if updates else s


def safe_generated_image_path(user_id: int, timestamp: str, idx: int) -> Path:
    safe_timestamp = re.sub(r"[^0-9A-Za-z_.-]+", "_", timestamp)
    path = GENERATED_DIR / f"{int(user_id)}_{safe_timestamp}_{int(idx)}.png"
    if GENERATED_DIR.resolve() not in path.resolve().parents:
        raise ValueError("Unsafe generated image path")
    return path


def save_generated_images(user_id: int, timestamp: str, images: list[bytes]) -> list[dict]:
    saved = []
    for idx, img in enumerate(images, start=1):
        path = safe_generated_image_path(user_id, timestamp, idx)
        path.write_bytes(img)
        saved.append({"path": path.as_posix(), "filename": f"novelai_{idx}.png", "index": idx})
    return saved


def safe_existing_generated_path(raw_path: str) -> Path | None:
    if not raw_path:
        return None
    path = Path(raw_path)
    if path.is_absolute() or ".." in path.parts:
        return None
    try:
        resolved_path = path.resolve()
        resolved_dir = GENERATED_DIR.resolve()
    except OSError:
        return None
    if resolved_dir not in resolved_path.parents:
        return None
    return path if path.exists() and path.is_file() else None
