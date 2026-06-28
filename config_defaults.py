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

AELITA_DESCRIPTION = (
    "raccoon girl, long black hair, pink eyes with vertical pupils, raccoon ears, "
    "striped raccoon tail, pale skin, constellation tattoo, oversized dark hoodie with purple accents, "
    "fingerless gloves"
)

QUICK_PRESETS = {
    "aelita": {
        "title": "🦝 Аэлита",
        "prompt": f"{AELITA_DESCRIPTION}, character portrait, expressive anime illustration, soft violet rim light, urban night ambience, detailed fabric folds",
    },
    "semi_realistic_anime": {
        "title": "🎨 Semi-realistic anime",
        "prompt": "semi-realistic anime style, refined facial anatomy, painterly skin shading, detailed hair strands, cinematic portrait lighting, depth of field, polished character illustration",
    },
    "arcane_fortiche": {
        "title": "🎬 Arcane / Fortiche",
        "prompt": "Fortiche-inspired painterly animation style, stylized 3d character look, bold brush texture, dramatic colored rim light, expressive face, cinematic composition, rich shadow shapes",
    },
    "ghibli_watercolor": {
        "title": "🌿 Ghibli watercolor",
        "prompt": "gentle Japanese animation background style, transparent watercolor wash, lush greenery, soft hand-painted clouds, warm nostalgic light, whimsical environmental details",
    },
    "manga_illustration": {
        "title": "🖋 Manga illustration",
        "prompt": "manga illustration, crisp black ink lineart, screentone shading, dynamic panel composition, expressive pose, speed lines, high contrast monochrome rendering",
    },
    "dark_fantasy_painting": {
        "title": "🕯 Dark fantasy painting",
        "prompt": "dark fantasy oil painting, chiaroscuro lighting, gothic atmosphere, weathered stone, candlelit mist, ornate armor details, dramatic painterly brushwork",
    },
    "sci_fantasy_macro": {
        "title": "🔬 Sci-fantasy macro",
        "prompt": "sci-fantasy macro photography look, bioluminescent crystal flora, tiny intricate mechanisms, shallow depth of field, iridescent particles, ultra close-up composition",
    },
    "vintage_storybook": {
        "title": "📖 Vintage storybook",
        "prompt": "vintage storybook illustration, muted ink and gouache, aged paper texture, decorative border, cozy fairytale scene, hand-drawn hatching, nostalgic print colors",
    },
    "art_nouveau": {
        "title": "🧵 Art nouveau",
        "prompt": "art nouveau illustration, flowing ornamental lines, botanical motifs, elegant poster composition, stained glass color palette, decorative frame, Alphonse Mucha inspired silhouette",
    },
    "painterly_illustration": {
        "title": "🖼 Painterly illustration",
        "prompt": "painterly illustration, visible brush strokes, cohesive color script, atmospheric perspective, soft edge control, warm key light, detailed focal point, concept art finish",
    },
}

MAX_EXTRA_CHARACTERS = 6

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
    artraccoon_character_prompt: str = ""
    artraccoon_character_uc: str = ""
    artraccoon_character_position: str = ""
    artraccoon_force_concat: bool = False
    extra_characters: list | None = None
    seed: int = -1
    uc_preset: str = "v4.5_full_heavy"
    negative_prompt: str = ""
    furry_mode: bool = False
    background_mode: bool = False
    add_quality_tags: bool = True
    uc_preset_id: int = 0
    dynamic_thresholding: bool = False
    last_prompt: str = ""
    pending_prompt: str = ""
    pending_original_prompt: str = ""
    last_image_path: str = ""
    pending_image_path: str = ""
    img2img_strength: float = 0.55
    img2img_noise: float = 0.10
    daily_generation_count: int = 0
    daily_generation_date: str = ""
    last_generation_started_at: str = ""
    paid_generations_balance: int = 0
    free_daily_used: int = 0
    free_daily_date: str = ""
    total_generations_used: int = 0
    artraccoon_vibe_enabled: bool = False

    # Служебный режим для текстового UX: append или replace.
    prompt_action: str = ""

    # Расширенные параметры. Можно менять через код/будущее меню.
    noise_schedule: str = "karras"
    smea: bool = False
    smea_dyn: bool = False
    cfg_rescale: float = 0.0
    variety_plus: bool = True
    nai_site_mode: bool = False
    use_coords: bool = False
    use_order: bool = True
    legacy_uc: bool = False
    v4_prompt: dict | None = None
    v4_negative_prompt: dict | None = None
    character_captions: list | None = None
    negative_character_captions: list | None = None
    infill_mask: str = ""
    nai_action: str = ""
    upscale_action: bool = False
    variation_action: bool = False

    def to_dict(self) -> dict:
        return asdict(self)
