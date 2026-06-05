"""计划文件 slug 生成模块，使用漫威和 DC 英雄名称。

为每个会话生成唯一的计划文件标识符（slug），由三个英雄名称组成，
用于创建易识别的计划文件名。
"""

from __future__ import annotations

import secrets
from pathlib import Path

# 计划文件存储目录
PLANS_DIR = Path.home() / ".novel" / "plans"

# 英雄名称列表
HERO_NAMES: list[str] = [
    # --- 漫威英雄 ---
    "iron-man",
    "spider-man",
    "captain-america",
    "thor",
    "hulk",
    "black-widow",
    "hawkeye",
    "black-panther",
    "doctor-strange",
    "scarlet-witch",
    "vision",
    "falcon",
    "war-machine",
    "ant-man",
    "wasp",
    "captain-marvel",
    "gamora",
    "star-lord",
    "groot",
    "rocket",
    "drax",
    "mantis",
    "nebula",
    "shang-chi",
    "moon-knight",
    "ms-marvel",
    "she-hulk",
    "echo",
    "wolverine",
    "cyclops",
    "storm",
    "jean-grey",
    "rogue",
    "beast",
    "nightcrawler",
    "colossus",
    "shadowcat",
    "jubilee",
    "cable",
    "deadpool",
    "bishop",
    "magik",
    "iceman",
    "archangel",
    "psylocke",
    "dazzler",
    "forge",
    "havok",
    "polaris",
    "emma-frost",
    "namor",
    "silver-surfer",
    "adam-warlock",
    "nova",
    "quasar",
    "sentry",
    "blue-marvel",
    "spectrum",
    "squirrel-girl",
    "cloak",
    "dagger",
    "punisher",
    "elektra",
    "luke-cage",
    "iron-fist",
    "jessica-jones",
    "daredevil",
    "blade",
    "ghost-rider",
    "morbius",
    "venom",
    "carnage",
    "silk",
    "spider-gwen",
    "miles-morales",
    "america-chavez",
    "kate-bishop",
    "yelena-belova",
    "white-tiger",
    "moon-girl",
    "devil-dinosaur",
    "amadeus-cho",
    "riri-williams",
    "kamala-khan",
    "sam-alexander",
    "nova-prime",
    "medusa",
    "black-bolt",
    "crystal",
    "karnak",
    "gorgon",
    "lockjaw",
    "quake",
    "mockingbird",
    "bobbi-morse",
    "maria-hill",
    "nick-fury",
    "phil-coulson",
    "winter-soldier",
    "us-agent",
    "patriot",
    "speed",
    "wiccan",
    "hulkling",
    "stature",
    "yellowjacket",
    "tigra",
    "hellcat",
    "valkyrie",
    "sif",
    "beta-ray-bill",
    "hercules",
    "wonder-man",
    "taskmaster",
    "domino",
    "cannonball",
    "sunspot",
    "wolfsbane",
    "warpath",
    "multiple-man",
    "banshee",
    "siryn",
    "monet",
    "rictor",
    "shatterstar",
    "longshot",
    "daken",
    "x-23",
    "fantomex",
    # --- DC 英雄 ---
    "batman",
    "superman",
    "wonder-woman",
    "flash",
    "aquaman",
    "green-lantern",
    "martian-manhunter",
    "cyborg",
    "hawkgirl",
    "green-arrow",
    "black-canary",
    "zatanna",
    "constantine",
    "shazam",
    "blue-beetle",
    "booster-gold",
    "firestorm",
    "atom",
    "hawkman",
    "plastic-man",
    "red-tornado",
    "starfire",
    "raven",
    "beast-boy",
    "robin",
    "nightwing",
    "batgirl",
    "batwoman",
    "red-hood",
    "signal",
    "orphan",
    "spoiler",
    "catwoman",
    "huntress",
    "supergirl",
    "superboy",
    "power-girl",
    "steel",
    "stargirl",
    "wildcat",
    "doctor-fate",
    "mister-terrific",
    "hourman",
    "sandman",
    "spectre",
    "phantom-stranger",
    "swamp-thing",
    "animal-man",
    "deadman",
    "vixen",
    "black-lightning",
    "static",
    "icon",
    "rocket-dc",
    "captain-atom",
    "fire",
    "ice",
    "elongated-man",
    "metamorpho",
    "black-hawk",
    "crimson-avenger",
    "doctor-mid-nite",
    "jakeem-thunder",
    "mister-miracle",
    "big-barda",
    "orion",
    "lightray",
    "forager",
    "killer-frost",
    "jessica-cruz",
    "simon-baz",
    "john-stewart",
    "guy-gardner",
    "kyle-rayner",
    "hal-jordan",
    "wally-west",
    "barry-allen",
    "jay-garrick",
    "impulse",
    "kid-flash",
    "donna-troy",
    "tempest",
    "aqualad",
    "miss-martian",
    "terra",
    "jericho",
    "ravager",
    "red-star",
    "pantha",
    "argent",
    "damage",
    "jade",
    "obsidian",
    "cyclone",
    "atom-smasher",
    "maxima",
    "starman",
    "liberty-belle",
]

# slug 缓存，避免重复生成
_slug_cache: dict[str, str] = {}


def seed_slug_cache(session_id: str, slug: str) -> None:
    """预热进程内 slug 缓存。

    将之前持久化的 slug 预加载到缓存中，避免重复生成。

    Args:
        session_id: 会话 ID。
        slug: 已存在的 slug 值。
    """
    _slug_cache[session_id] = slug


def get_or_create_slug(session_id: str) -> str:
    """获取或创建指定会话的计划文件 slug。

    若缓存中已存在则直接返回，否则生成新的 slug。
    slug 由三个英雄名称组合而成，如 `iron-man-spider-man-thor`。

    Args:
        session_id: 会话 ID。

    Returns:
        计划文件 slug 字符串。
    """
    if session_id in _slug_cache:
        return _slug_cache[session_id]
    PLANS_DIR.mkdir(parents=True, exist_ok=True)
    slug = ""
    for _ in range(20):
        # 随机选择三个英雄名称组合
        words = [secrets.choice(HERO_NAMES) for _ in range(3)]
        slug = "-".join(words)
        if not (PLANS_DIR / f"{slug}.md").exists():
            break
    else:
        # 20 次尝试均冲突，追加会话前缀保证唯一性
        slug = f"{slug}-{session_id[:8]}"
    _slug_cache[session_id] = slug
    return slug


def get_plan_file_path(session_id: str) -> Path:
    """获取指定会话的计划文件路径。

    Args:
        session_id: 会话 ID。

    Returns:
        计划文件的完整路径。
    """
    return PLANS_DIR / f"{get_or_create_slug(session_id)}.md"


def read_plan_file(session_id: str) -> str | None:
    """读取指定会话的计划文件内容。

    Args:
        session_id: 会话 ID。

    Returns:
        计划文件内容字符串，若文件不存在则返回 None。
    """
    path = get_plan_file_path(session_id)
    if path.exists():
        return path.read_text(encoding="utf-8")
    return None