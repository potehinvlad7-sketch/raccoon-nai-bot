import base64
import io
import logging
import copy
import zipfile
from typing import Awaitable, Callable, Optional

import httpx

from config_defaults import MODELS, UC_PRESETS, UserSettings

log = logging.getLogger(__name__)
SAFE_DEFAULT_MODEL = "nai-diffusion-4-5-full"
SECRET_PAYLOAD_KEYS = {"authorization", "token", "api_token", "access_token", "secret"}
SITE_MODE_STEPS = 28
SITE_MODE_SCALE = 7.5
SITE_MODE_CFG_RESCALE = 0.18
SITE_MODE_SAMPLER = "k_dpmpp_sde"
SITE_MODE_NOISE_SCHEDULE = "karras"


def _join_prompt_parts(parts: list[str]) -> str:
    return ", ".join(part.strip() for part in parts if part and part.strip())

def _is_v4_model(model: str) -> bool:
    return model.startswith(("nai-diffusion-4", "nai-diffusion-4-5"))

def _character_caption(prompt: str, position: str = "") -> dict:
    caption = {"char_caption": prompt.strip()}
    pos = position.strip()
    if pos:
        caption["position"] = pos
    return caption

def _extra_characters(settings: UserSettings) -> list[dict]:
    items = settings.extra_characters if isinstance(settings.extra_characters, list) else []
    chars = []
    for item in items:
        if not isinstance(item, dict):
            continue
        prompt = str(item.get("prompt") or "").strip()
        uc = str(item.get("uc") or "").strip()
        position = str(item.get("position") or "").strip()
        if prompt or uc:
            chars.append({"prompt": prompt, "uc": uc, "position": position})
    return chars

def sanitize_payload(payload: dict) -> dict:
    """Return a deep-copied payload with any accidental secret-like fields removed."""
    def clean(value):
        if isinstance(value, dict):
            return {
                key: clean(item)
                for key, item in value.items()
                if str(key).lower() not in SECRET_PAYLOAD_KEYS
            }
        if isinstance(value, list):
            return [clean(item) for item in value]
        return value
    return clean(copy.deepcopy(payload))

def payload_summary(payload: dict, settings: UserSettings | None = None) -> dict:
    parameters = payload.get("parameters", {})
    v4_prompt = parameters.get("v4_prompt", {})
    v4_negative = parameters.get("v4_negative_prompt", {})
    uc_preset = parameters.get("ucPreset")
    if settings:
        uc_preset = f"{settings.uc_preset} / ucPreset={uc_preset}"
    return {
        "model": payload.get("model"),
        "params_version": parameters.get("params_version"),
        "width": parameters.get("width"),
        "height": parameters.get("height"),
        "steps": parameters.get("steps"),
        "scale": parameters.get("scale"),
        "cfg_rescale": parameters.get("cfg_rescale"),
        "sampler": parameters.get("sampler"),
        "qualityToggle": parameters.get("qualityToggle"),
        "dynamic_thresholding": parameters.get("dynamic_thresholding"),
        "variety_plus": parameters.get("variety_plus"),
        "noise_schedule": parameters.get("noise_schedule"),
        "seed": parameters.get("seed", "random/omitted"),
        "uc_preset": uc_preset,
        "negative_prompt": parameters.get("negative_prompt", ""),
        "n_samples": parameters.get("n_samples"),
        "v4_prompt_fields": list(v4_prompt.keys()) if isinstance(v4_prompt, dict) else [],
        "v4_negative_prompt_fields": list(v4_negative.keys()) if isinstance(v4_negative, dict) else [],
        "character_payload": bool(
            isinstance(v4_prompt, dict)
            and v4_prompt.get("caption", {}).get("char_captions")
        ),
        "site_mode": bool(settings.nai_site_mode) if settings else None,
    }

class NovelAIError(RuntimeError):
    pass

class NovelAIClient:
    """
    Мини-клиент NovelAI Image API.

    Важно: NovelAI может менять структуру API. Если API ответит ошибкой,
    лог ошибки будет показан в Telegram. Править чаще всего надо build_payload().
    """

    def __init__(self, token: str, default_model: str = "", proxy_url: str = ""):
        self.token = token.strip()
        self.default_model = default_model.strip()
        self.proxy_url = proxy_url.strip()
        self.base_url = "https://image.novelai.net"
        self.last_payload: dict = {}

    def _headers(self) -> dict:
        return {
            "Authorization": f"Bearer {self.token}",
            "Accept": "application/x-zip-compressed, application/zip, image/png, application/json",
            "Content-Type": "application/json",
            "User-Agent": "ArtRaccoon-NovelAI-Telegram-Bot/0.1",
        }

    def build_prompt(self, prompt: str, settings: UserSettings) -> str:
        p = prompt.strip()
        prefixes = []
        if settings.furry_mode:
            prefixes.append("fur dataset")
        if settings.background_mode:
            prefixes.append("background dataset")
        if settings.add_quality_tags:
            prefixes.append("best quality, amazing quality")
        if prefixes:
            p = ", ".join(prefixes) + ", " + p
        return p

    def build_payload(
        self,
        prompt: str,
        settings: UserSettings,
        image_b64: Optional[str] = None,
        mask_b64: Optional[str] = None,
        force_character_concat: bool = False,
    ) -> dict:
        model = self.default_model or MODELS.get(settings.model_name) or SAFE_DEFAULT_MODEL
        is_v4_model = _is_v4_model(model)
        character_prompt = settings.artraccoon_character_prompt.strip() if settings.artraccoon_mode else ""
        character_uc = (
            settings.artraccoon_character_uc.strip()
            or settings.artraccoon_character_negative.strip()
        ) if settings.artraccoon_mode else ""
        extra_characters = _extra_characters(settings)
        has_any_character = bool(character_prompt or any(ch["prompt"] for ch in extra_characters))
        has_character_payload = bool(
            is_v4_model
            and has_any_character
            and not settings.artraccoon_force_concat
            and not force_character_concat
        )

        uc_parts = [UC_PRESETS.get(settings.uc_preset, "")]
        if settings.artraccoon_mode:
            uc_parts.append(settings.artraccoon_base_uc)
            if not has_character_payload:
                uc_parts.append(character_uc)
        if not has_character_payload:
            uc_parts.extend(ch["uc"] for ch in extra_characters)
        if settings.negative_prompt.strip():
            uc_parts.append(settings.negative_prompt.strip())
        uc = _join_prompt_parts(uc_parts)

        prompt_for_payload = prompt
        concat_character_prompts = []
        if settings.artraccoon_mode and character_prompt and not has_character_payload:
            concat_character_prompts.append(character_prompt)
        if not has_character_payload:
            concat_character_prompts.extend(ch["prompt"] for ch in extra_characters)
        if concat_character_prompts:
            prompt_for_payload = _join_prompt_parts([prompt, *concat_character_prompts])
        built_prompt = self.build_prompt(prompt_for_payload, settings)

        parameters = {
            "params_version": 3,
            "width": settings.width,
            "height": settings.height,
            "scale": settings.scale,
            "sampler": settings.sampler,
            "steps": settings.steps,
            "n_samples": settings.n_samples,
            "ucPreset": settings.uc_preset_id,
            "qualityToggle": settings.add_quality_tags,
            "dynamic_thresholding": settings.dynamic_thresholding,
            "variety_plus": settings.variety_plus,
            "cfg_rescale": settings.cfg_rescale,
            "noise_schedule": settings.noise_schedule,
            "negative_prompt": uc,
        }
        if is_v4_model:
            char_captions = []
            negative_char_captions = []
            if has_character_payload:
                if character_prompt:
                    char_captions.append(_character_caption(character_prompt, settings.artraccoon_character_position))
                if character_uc:
                    negative_char_captions.append(_character_caption(character_uc, settings.artraccoon_character_position))
                for character in extra_characters:
                    if character["prompt"]:
                        char_captions.append(_character_caption(character["prompt"], character["position"]))
                    if character["uc"]:
                        negative_char_captions.append(_character_caption(character["uc"], character["position"]))
            parameters["v4_prompt"] = {
                "caption": {
                    "base_caption": built_prompt,
                    "char_captions": char_captions,
                },
                "use_coords": settings.use_coords,
                "use_order": settings.use_order,
            }
            parameters["v4_negative_prompt"] = {
                "caption": {
                    "base_caption": uc,
                    "char_captions": negative_char_captions,
                },
                "use_coords": settings.use_coords,
                "use_order": False,
                "legacy_uc": settings.legacy_uc,
            }
            if settings.character_captions is not None:
                parameters["v4_prompt"]["caption"]["char_captions"] = settings.character_captions
            if settings.negative_character_captions is not None:
                parameters["v4_negative_prompt"]["caption"]["char_captions"] = settings.negative_character_captions
            if settings.v4_prompt is not None:
                parameters["v4_prompt"] = settings.v4_prompt
            if settings.v4_negative_prompt is not None:
                parameters["v4_negative_prompt"] = settings.v4_negative_prompt
        else:
            parameters["sm"] = settings.smea
            parameters["sm_dyn"] = settings.smea_dyn
        if settings.seed != -1:
            parameters["seed"] = settings.seed

        action = "generate"
        if image_b64:
            action = "img2img"
            parameters.update({
                "image": image_b64,
                "strength": settings.img2img_strength,
                "noise": settings.img2img_noise,
            })
        if mask_b64:
            action = "infill"
            parameters["mask"] = mask_b64

        return {
            "input": built_prompt,
            "model": model,
            "action": action,
            "parameters": parameters,
        }


    def safe_prompt_preview(self, prompt: str, settings: UserSettings) -> dict:
        payload = self.build_payload(prompt, settings)
        parameters = payload["parameters"]
        char_captions = parameters.get("v4_prompt", {}).get("caption", {}).get("char_captions", [])
        neg_char_captions = parameters.get("v4_negative_prompt", {}).get("caption", {}).get("char_captions", [])
        return {
            "model": payload["model"],
            "base_prompt_length": len(parameters.get("v4_prompt", {}).get("caption", {}).get("base_caption", payload["input"])),
            "character_prompt_length": len(settings.artraccoon_character_prompt.strip()),
            "has_character_payload": bool(char_captions),
            "negative_base_length": len(parameters.get("negative_prompt", "")),
            "negative_character_length": len(settings.artraccoon_character_uc.strip() or settings.artraccoon_character_negative.strip()),
            "negative_character_payload": bool(neg_char_captions),
        }

    def debug_settings(self, settings: UserSettings) -> dict:
        """Return the effective NovelAI model and generation parameters for diagnostics."""
        payload = self.build_payload("debug prompt", settings)
        parameters = payload["parameters"]
        return {
            "endpoint": f"{self.base_url}/ai/generate-image",
            "model": payload["model"],
            "action": payload["action"],
            "width": parameters["width"],
            "height": parameters["height"],
            "steps": parameters["steps"],
            "scale": parameters["scale"],
            "sampler": parameters["sampler"],
            "noise_schedule": parameters["noise_schedule"],
            "cfg_rescale": parameters["cfg_rescale"],
            "n_samples": parameters["n_samples"],
            "ucPreset": parameters["ucPreset"],
            "params_version": parameters["params_version"],
            "qualityToggle": parameters["qualityToggle"],
            "dynamic_thresholding": parameters["dynamic_thresholding"],
            "variety_plus": parameters.get("variety_plus"),
            "negative_prompt": parameters["negative_prompt"],
            "v4_prompt": "v4_prompt" in parameters,
            "v4_negative_prompt": "v4_negative_prompt" in parameters,
        }

    async def generate(
        self,
        prompt: str,
        settings: UserSettings,
        image_bytes: Optional[bytes] = None,
        mask_bytes: Optional[bytes] = None,
        on_character_payload_fallback: Optional[Callable[[], Awaitable[None]]] = None,
    ) -> list[bytes]:
        if not self.token or self.token.startswith("PASTE_"):
            raise NovelAIError("NOVELAI_TOKEN не настроен. Добавьте токен в переменные окружения.")

        image_b64 = base64.b64encode(image_bytes).decode("utf-8") if image_bytes else None
        mask_b64 = base64.b64encode(mask_bytes).decode("utf-8") if mask_bytes else None
        payload = self.build_payload(prompt, settings, image_b64=image_b64, mask_b64=mask_b64)
        self.last_payload = sanitize_payload(payload)
        summary = payload_summary(payload, settings)
        log.info(
            "NovelAI payload summary: model=%s, size=%sx%s, sampler=%s, noise=%s, steps=%s, scale=%s, seed=%s",
            summary["model"],
            summary["width"],
            summary["height"],
            summary["sampler"],
            summary["noise_schedule"],
            summary["steps"],
            summary["scale"],
            summary["seed"],
        )

        client_kwargs = {"timeout": 180}
        if self.proxy_url:
            client_kwargs["proxy"] = self.proxy_url

        async with httpx.AsyncClient(**client_kwargs) as client:
            r = await client.post(
                f"{self.base_url}/ai/generate-image",
                headers=self._headers(),
                json=payload,
            )

            if r.status_code == 500 and self._payload_uses_character_payload(payload):
                log.warning(
                    "NovelAI returned 500 for character payload, retrying with fallback concat: %s",
                    self._safe_response_diagnostics(r),
                )
                if on_character_payload_fallback:
                    await on_character_payload_fallback()
                payload = self.build_payload(
                    prompt,
                    settings,
                    image_b64=image_b64,
                    mask_b64=mask_b64,
                    force_character_concat=True,
                )
                self.last_payload = sanitize_payload(payload)
                r = await client.post(
                    f"{self.base_url}/ai/generate-image",
                    headers=self._headers(),
                    json=payload,
                )

        if r.status_code >= 400:
            error_text = self._friendly_api_error(r)
            log.warning("NovelAI image API error: %s", self._safe_response_diagnostics(r))
            raise NovelAIError(error_text)

        content_type = r.headers.get("content-type", "")
        data = r.content

        if "zip" in content_type or data[:2] == b"PK":
            return self._extract_images_from_zip(data)
        if data.startswith(b"\x89PNG"):
            return [data]

        raise NovelAIError(f"Неожиданный ответ NovelAI: content-type={content_type or 'unknown'}")


    @staticmethod
    def _payload_uses_character_payload(payload: dict) -> bool:
        parameters = payload.get("parameters", {})
        return bool(
            parameters.get("v4_prompt", {})
            .get("caption", {})
            .get("char_captions")
        )

    def _extract_images_from_zip(self, data: bytes) -> list[bytes]:
        images: list[bytes] = []
        with zipfile.ZipFile(io.BytesIO(data)) as zf:
            for name in zf.namelist():
                lower = name.lower()
                if lower.endswith((".png", ".jpg", ".jpeg", ".webp")):
                    images.append(zf.read(name))
        if not images:
            raise NovelAIError("В zip-ответе NovelAI не нашла картинок")
        return images


    def _safe_response_diagnostics(self, response: httpx.Response) -> str:
        content_type = response.headers.get("content-type", "unknown") or "unknown"
        body = response.text.replace("\r", " ").replace("\n", " ")[:500]
        if not body:
            body = "<empty>"
        return (
            f"status={response.status_code}; "
            f"content-type={content_type}; "
            f"body={body}"
        )


    def _friendly_api_error(self, response: httpx.Response) -> str:
        messages = {
            400: "Некорректный запрос к NovelAI. Проверьте промт и настройки генерации.",
            401: "NovelAI отклонил авторизацию. Проверьте NOVELAI_TOKEN.",
            402: "NovelAI сообщает, что для генерации недостаточно доступа или Anlas.",
            403: "NovelAI запретил запрос. Проверьте права токена или доступность сервиса.",
            429: "Слишком много запросов к NovelAI. Попробуйте немного позже.",
            500: "На стороне NovelAI произошла ошибка. Попробуйте позже.",
            502: "NovelAI временно недоступен. Попробуйте позже.",
            503: "NovelAI временно недоступен. Попробуйте позже.",
            504: "NovelAI не ответил вовремя. Попробуйте позже.",
        }
        diagnostics = self._safe_response_diagnostics(response)
        hint = " Это безопасная диагностика без токена: " + diagnostics
        if response.status_code in messages:
            return messages[response.status_code] + hint
        return f"NovelAI API вернул ошибку {response.status_code}. Попробуйте позже." + hint
