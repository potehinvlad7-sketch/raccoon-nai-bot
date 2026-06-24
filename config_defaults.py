from dataclasses import dataclass, asdict

MODELS = {
    "V4.5 Full": "nai-diffusion-4-5-full",
    "V4.5 Curated": "nai-diffusion-4-5-curated",
    "V4 Full": "nai-diffusion-4-full",
    "V4 Curated": "nai-diffusion-4-curated",
    "Anime V3": "nai-diffusion-3",
}

# Не все sampler могут быть доступны для каждой модели.
NOISE_SCHEDULES = ["karras", "native", "exponential", "polyexponential"]

SAMPLERS = [
    "k_euler",
    "k_euler_ancestral",
    "k_dpmpp_2s_ancestral",
    "k_dpmpp_2m",
    "k_dpmpp_sde",
    "ddim",
]

RESOLUTIONS = {
    "Portrait 832x1216": (832, 1216),
    "Landscape 1216x832": (1216, 832),
    "Square 1024x1024": (1024, 1024),
    "Tall 768x1344": (768, 1344),
    "Wide 1344x768": (1344, 768),
    "Small 512x768": (512, 768),
}

QUICK_PRESETS = {
    "aelita": {
        "title": "Аэлита",
        "prompt": "Aelita, elegant retro-futuristic princess from Mars, silver crown, red desert palace, art nouveau, cinematic light",
    },
    "anime_portrait": {
        "title": "Anime portrait",
        "prompt": "anime portrait, expressive eyes, soft rim light, detailed hair, clean lineart, beautiful face, simple background",
    },
    "fantasy_tarot": {
        "title": "Fantasy tarot",
        "prompt": "fantasy tarot card, ornate golden frame, mystical character, symbolic details, glowing magic, highly detailed illustration",
    },
    "watercolor_raccoon": {
        "title": "Watercolor raccoon",
        "prompt": "cute raccoon, watercolor illustration, soft paper texture, gentle colors, cozy forest, whimsical mood",
    },
    "manga_action": {
        "title": "Manga action",
        "prompt": "manga action scene, dynamic pose, speed lines, dramatic perspective, impact frame, black and white ink",
    },
    "arcane_style": {
        "title": "Arcane style",
        "prompt": "stylized fantasy character portrait, painterly animation look, dramatic lighting, ornate details, rich colors",
    },
}

UC_PRESETS = {
    "none": "",
    "v4.5_full_heavy": "lowres, artistic error, film grain, scan artifacts, worst quality, bad quality, jpeg artifacts, very displeasing, chromatic aberration, dithering, halftone, screentone, multiple views, logo, too many watermarks, negative space, blank page",
    "v4.5_full_light": "lowres, artistic error, scan artifacts, worst quality, bad quality, jpeg artifacts, multiple views, very displeasing, too many watermarks, negative space, blank page",
    "v4.5_full_human": "lowres, artistic error, film grain, scan artifacts, worst quality, bad quality, jpeg artifacts, very displeasing, chromatic aberration, dithering, halftone, screentone, multiple views, logo, too many watermarks, negative space, blank page, @_@, mismatched pupils, glowing eyes, bad anatomy",
    "v4.5_curated_heavy": "blurry, lowres, upscaled, artistic error, film grain, scan artifacts, worst quality, bad quality, jpeg artifacts, very displeasing, chromatic aberration, halftone, multiple views, logo, too many watermarks, negative space, blank page",
    "v4.5_curated_light": "blurry, lowres, upscaled, artistic error, scan artifacts, jpeg artifacts, logo, too many watermarks, negative space, blank page",
}

@dataclass
class UserSettings:
    model_name: str = "V4.5 Full"
    width: int = 832
    height: int = 1216
    steps: int = 23
    scale: float = 4.0
    sampler: str = "k_euler_ancestral"
    n_samples: int = 1
    pro_mode: bool = False
    artraccoon_mode: bool = False
    artraccoon_base_prompt: str = ""
    artraccoon_base_uc: str = ""
    artraccoon_character_negative: str = ""
    seed: int = -1
    uc_preset: str = "v4.5_full_heavy"
    negative_prompt: str = ""
    furry_mode: bool = False
    background_mode: bool = False
    add_quality_tags: bool = True
    last_prompt: str = ""
    pending_prompt: str = ""
    pending_original_prompt: str = ""
    last_image_path: str = ""
    pending_image_path: str = ""
    img2img_strength: float = 0.55
    img2img_noise: float = 0.10

    # Служебный режим для текстового UX: append или replace.
    prompt_action: str = ""

    # Расширенные параметры. Можно менять через код/будущее меню.
    noise_schedule: str = "karras"
    smea: bool = False
    smea_dyn: bool = False
    cfg_rescale: float = 0.0
    variety_plus: bool = False

    def to_dict(self) -> dict:
        return asdict(self)
