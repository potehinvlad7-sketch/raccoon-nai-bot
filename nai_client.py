import base64
import io
import zipfile
from typing import Optional

import httpx

from config_defaults import MODELS, UC_PRESETS, UserSettings

class NovelAIError(RuntimeError):
    pass

class NovelAIClient:
    """
    Мини-клиент NovelAI Image API.

    Важно: NovelAI может менять структуру API. Если API ответит ошибкой,
    лог ошибки будет показан в Telegram. Править чаще всего надо build_payload().
    """

    def __init__(self, token: str = ""):
        self.token = token.strip()
        self.base_url = "https://image.novelai.net"
        self.api_url = "https://api.novelai.net"

    def _headers(self, token: str | None = None) -> dict:
        auth_token = (token if token is not None else self.token).strip()
        return {
            "Authorization": f"Bearer {auth_token}",
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
    ) -> dict:
        model = MODELS.get(settings.model_name, "nai-diffusion-4-5-full")
        uc = UC_PRESETS.get(settings.uc_preset, "")
        if settings.negative_prompt.strip():
            uc = (uc + ", " + settings.negative_prompt.strip()).strip(", ")

        parameters = {
            "width": settings.width,
            "height": settings.height,
            "scale": settings.scale,
            "sampler": settings.sampler,
            "steps": settings.steps,
            "n_samples": settings.n_samples,
            "ucPreset": 0,
            "qualityToggle": settings.add_quality_tags,
            "sm": settings.smea,
            "sm_dyn": settings.smea_dyn,
            "dynamic_thresholding": False,
            "controlnet_strength": 1.0,
            "legacy": False,
            "add_original_image": True,
            "cfg_rescale": settings.cfg_rescale,
            "noise_schedule": settings.noise_schedule,
            "negative_prompt": uc,
            "seed": None if settings.seed == -1 else settings.seed,
        }

        action = "generate"
        if image_b64:
            action = "img2img"
            parameters.update({
                "image": image_b64,
                "strength": 0.55,
                "noise": 0.10,
            })
        if mask_b64:
            action = "infill"
            parameters["mask"] = mask_b64

        return {
            "input": self.build_prompt(prompt, settings),
            "model": model,
            "action": action,
            "parameters": parameters,
        }

    async def validate_token(self, token: str | None = None) -> bool:
        auth_token = (token if token is not None else self.token).strip()
        if not auth_token or auth_token.startswith("PASTE_"):
            return False

        try:
            async with httpx.AsyncClient(timeout=30) as client:
                r = await client.get(
                    f"{self.api_url}/user/subscription",
                    headers=self._headers(auth_token),
                )
        except httpx.HTTPError:
            return False

        if r.status_code in (401, 403):
            return False
        return 200 <= r.status_code < 300

    async def generate(
        self,
        prompt: str,
        settings: UserSettings,
        image_bytes: Optional[bytes] = None,
        mask_bytes: Optional[bytes] = None,
    ) -> list[bytes]:
        if not self.token or self.token.startswith("PASTE_"):
            raise NovelAIError("NAI_TOKEN не заполнен в .env")

        image_b64 = base64.b64encode(image_bytes).decode("utf-8") if image_bytes else None
        mask_b64 = base64.b64encode(mask_bytes).decode("utf-8") if mask_bytes else None
        payload = self.build_payload(prompt, settings, image_b64=image_b64, mask_b64=mask_b64)

        async with httpx.AsyncClient(timeout=180) as client:
            r = await client.post(
                f"{self.base_url}/ai/generate-image",
                headers=self._headers(),
                json=payload,
            )

        if r.status_code >= 400:
            text = r.text[:2000]
            raise NovelAIError(f"NovelAI API error {r.status_code}: {text}")

        content_type = r.headers.get("content-type", "")
        data = r.content

        if "zip" in content_type or data[:2] == b"PK":
            return self._extract_images_from_zip(data)
        if data.startswith(b"\x89PNG"):
            return [data]

        raise NovelAIError(f"Неожиданный ответ NovelAI: content-type={content_type}, first_bytes={data[:20]!r}")

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
