"""Local prompt helpers for NovelAI tag-style prompts."""

import re

STYLE_BOOSTERS = ["anime illustration", "best quality", "detailed background", "soft lighting"]

RU_TAGS = {
    "девушка": "girl", "парень": "boy", "женщина": "woman", "мужчина": "man",
    "ребёнок": "child", "ребенок": "child", "енотка": "raccoon girl", "енот": "raccoon",
    "лиса": "fox", "кошка": "cat", "волк": "wolf", "дракон": "dragon",
    "библиотека": "library", "лес": "forest", "город": "city", "руины": "ruins",
    "мастерская": "workshop", "кафе": "cafe", "кофе": "coffee", "пирожные": "pastries",
    "космос": "space", "звёзды": "stars", "звезды": "stars", "магия": "magic",
    "ведьма": "witch", "принцесса": "princess", "воин": "warrior", "меч": "sword",
    "плащ": "cloak", "худи": "hoodie", "перчатки": "gloves", "хвост": "tail", "уши": "ears",
    "акварель": "watercolor", "манга": "manga", "аниме": "anime", "реализм": "realism",
    "кинематографично": "cinematic", "мягкий свет": "soft lighting", "драматический свет": "dramatic lighting",
    "ночь": "night", "день": "day", "закат": "sunset", "рассвет": "sunrise",
    "дождь": "rain", "снег": "snow", "туман": "fog", "уютно": "cozy",
    "красиво": "beautiful", "детально": "detailed",
}

_CYRILLIC_RE = re.compile(r"[а-яё]", re.IGNORECASE)
_ASCII_TAG_RE = re.compile(r"^[\w\s,{}():.+\-'/]+$")


def looks_like_english_tags(text: str) -> bool:
    """Return True when text already looks like comma-separated English tags."""
    stripped = text.strip()
    return "," in stripped and not _CYRILLIC_RE.search(stripped) and bool(_ASCII_TAG_RE.match(stripped))


def natural_to_nai_tags(text: str) -> str:
    """Convert a small set of Russian natural prompt words to English NAI tags."""
    source = " ".join(text.replace("\n", " ").split())
    if not source:
        return ""
    if looks_like_english_tags(source):
        return source

    lowered = source.lower()
    tags: list[str] = []
    for src in sorted(RU_TAGS, key=len, reverse=True):
        if re.search(rf"(?<![а-яё]){re.escape(src)}(?![а-яё])", lowered):
            tag = RU_TAGS[src]
            if tag not in tags:
                tags.append(tag)

    if not tags and not _CYRILLIC_RE.search(source):
        tags.extend(part.strip() for part in source.split(",") if part.strip())

    for booster in STYLE_BOOSTERS:
        if booster not in tags:
            tags.append(booster)
    return ", ".join(tags)
