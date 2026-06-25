"""Local prompt helpers for NovelAI tag-style prompts."""

import json
import re
import unicodedata
from pathlib import Path
from typing import Any

DICTIONARY_PATH = Path("data/learned_dictionary.json")
DEFAULT_DICTIONARY = {
    "ru_to_tags": {},
    "tag_frequency": {},
    "pending_suggestions": [],
    "rejected_tags": [],
}

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

JUNK_TAGS = {
    "masterpiece", "best quality", "highres", "absurdres", "very aesthetic",
    "cinematic lighting", "ultra detailed", "low quality", "worst quality",
    "bad anatomy", "watermark", "signature", "text", "amazing quality",
}

_CYRILLIC_RE = re.compile(r"[а-яё]", re.IGNORECASE)
_ASCII_TAG_RE = re.compile(r"^[\w\s,{}():.+\-'/\[\]]+$")
_WORD_RE = re.compile(r"[а-яё]+", re.IGNORECASE)

_TRANSLIT = str.maketrans({
    "а": "a", "б": "b", "в": "v", "г": "g", "д": "d", "е": "e", "ё": "yo", "ж": "zh", "з": "z", "и": "i", "й": "y",
    "к": "k", "л": "l", "м": "m", "н": "n", "о": "o", "п": "p", "р": "r", "с": "s", "т": "t", "у": "u", "ф": "f",
    "х": "h", "ц": "ts", "ч": "ch", "ш": "sh", "щ": "sch", "ъ": "", "ы": "y", "ь": "", "э": "e", "ю": "yu", "я": "ya",
})


def _normalize_dictionary(data: Any) -> dict:
    result = dict(DEFAULT_DICTIONARY)
    if isinstance(data, dict):
        for key in result:
            if key in data:
                result[key] = data[key]
    if not isinstance(result["ru_to_tags"], dict):
        result["ru_to_tags"] = {}
    result["ru_to_tags"] = {
        str(k).strip(): [str(t).strip().lower() for t in v if is_useful_tag(str(t))]
        for k, v in result["ru_to_tags"].items() if str(k).strip() and isinstance(v, list)
    }
    result["tag_frequency"] = {str(k): int(v) for k, v in result["tag_frequency"].items() if str(k)} if isinstance(result["tag_frequency"], dict) else {}
    for key in ("pending_suggestions", "rejected_tags"):
        values = result[key] if isinstance(result[key], list) else []
        result[key] = sorted({str(x).strip().lower() for x in values if is_useful_tag(str(x))})
    return result


def load_learned_dictionary() -> dict:
    DICTIONARY_PATH.parent.mkdir(parents=True, exist_ok=True)
    if not DICTIONARY_PATH.exists():
        save_learned_dictionary(DEFAULT_DICTIONARY)
    try:
        return _normalize_dictionary(json.loads(DICTIONARY_PATH.read_text(encoding="utf-8")))
    except (json.JSONDecodeError, OSError):
        return dict(DEFAULT_DICTIONARY)


def save_learned_dictionary(data: dict) -> None:
    DICTIONARY_PATH.parent.mkdir(parents=True, exist_ok=True)
    DICTIONARY_PATH.write_text(json.dumps(_normalize_dictionary(data), ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def is_useful_tag(tag: str) -> bool:
    tag = " ".join(tag.strip().lower().split())
    if not tag or tag in JUNK_TAGS or len(tag) > 60 or tag.isdigit():
        return False
    if any(unicodedata.category(ch)[0] == "C" for ch in tag):
        return False
    if len(tag.split()) > 8:
        return False
    return bool(re.fullmatch(r"[a-z0-9][a-z0-9 _'\-/]*", tag))


def parse_english_tags(prompt: str) -> list[str]:
    tags: list[str] = []
    for raw in prompt.replace("\n", ",").split(","):
        tag = raw.strip().lower()
        tag = re.sub(r"^[\[{(]+|[\]})]+$", "", tag).strip()
        tag = re.sub(r"^(?:\d+(?:\.\d+)?::)?(.+?)::$", r"\1", tag).strip()
        tag = re.sub(r"^(.+?):\d+(?:\.\d+)?$", r"\1", tag).strip()
        tag = " ".join(tag.split())
        if is_useful_tag(tag) and tag not in tags:
            tags.append(tag)
    return tags


def learn_from_english_prompt(prompt: str) -> list[str]:
    tags = parse_english_tags(prompt)
    if not tags:
        return []
    data = load_learned_dictionary()
    approved = {tag for values in data["ru_to_tags"].values() for tag in values}
    rejected = set(data["rejected_tags"])
    pending = set(data["pending_suggestions"])
    candidates: list[str] = []
    for tag in tags:
        data["tag_frequency"][tag] = data["tag_frequency"].get(tag, 0) + 1
        if tag not in approved and tag not in rejected:
            pending.add(tag)
            candidates.append(tag)
    data["pending_suggestions"] = sorted(pending)
    save_learned_dictionary(data)
    return candidates


def add_learned_mapping(ru_phrase: str, tags: list[str]) -> None:
    data = load_learned_dictionary()
    key = " ".join(ru_phrase.strip().lower().split())
    clean = [tag for tag in parse_english_tags(", ".join(tags)) if tag not in data["rejected_tags"]]
    if not key or not clean:
        return
    existing = data["ru_to_tags"].setdefault(key, [])
    for tag in clean:
        if tag not in existing:
            existing.append(tag)
        if tag in data["pending_suggestions"]:
            data["pending_suggestions"].remove(tag)
    save_learned_dictionary(data)


def reject_tags(tags: list[str]) -> None:
    data = load_learned_dictionary()
    rejected = set(data["rejected_tags"])
    rejected.update(tag for tag in tags if is_useful_tag(tag))
    data["rejected_tags"] = sorted(rejected)
    data["pending_suggestions"] = [tag for tag in data["pending_suggestions"] if tag not in rejected]
    save_learned_dictionary(data)


def looks_like_english_tags(text: str) -> bool:
    stripped = text.strip()
    return "," in stripped and not _CYRILLIC_RE.search(stripped) and bool(_ASCII_TAG_RE.match(stripped))


def transliterate_ru(text: str) -> str:
    return text.lower().translate(_TRANSLIT)


def natural_to_nai_tags(text: str) -> str:
    source = " ".join(text.replace("\n", " ").split())
    if not source:
        return ""
    if looks_like_english_tags(source):
        return source
    lowered = source.lower()
    tags: list[str] = []
    consumed: list[tuple[int, int]] = []
    combined = {**RU_TAGS, **load_learned_dictionary()["ru_to_tags"]}
    for src in sorted(combined, key=len, reverse=True):
        for match in re.finditer(rf"(?<![а-яё]){re.escape(src)}(?![а-яё])", lowered):
            if any(not (match.end() <= a or match.start() >= b) for a, b in consumed):
                continue
            values = combined[src] if isinstance(combined[src], list) else [combined[src]]
            for tag in values:
                if tag not in tags:
                    tags.append(tag)
            consumed.append((match.start(), match.end()))
    for match in _WORD_RE.finditer(lowered):
        if not any(match.start() >= a and match.end() <= b for a, b in consumed):
            unknown = transliterate_ru(match.group(0))
            if unknown and unknown not in tags:
                tags.append(unknown)
    if not tags and not _CYRILLIC_RE.search(source):
        tags.extend(part.strip().lower() for part in source.split(",") if is_useful_tag(part))
    return ", ".join(tags)


def has_unknown_russian(text: str) -> bool:
    source = " ".join(text.replace("\n", " ").split()).lower()
    if not _CYRILLIC_RE.search(source):
        return False
    consumed: list[tuple[int, int]] = []
    for src in sorted({**RU_TAGS, **load_learned_dictionary()["ru_to_tags"]}, key=len, reverse=True):
        for match in re.finditer(rf"(?<![а-яё]){re.escape(src)}(?![а-яё])", source):
            consumed.append((match.start(), match.end()))
    return any(not any(m.start() >= a and m.end() <= b for a, b in consumed) for m in _WORD_RE.finditer(source))
