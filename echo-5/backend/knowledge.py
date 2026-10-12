import json
import re
from pathlib import Path

KNOWLEDGE_DIR = Path(__file__).resolve().parent / "knowledge"
INDEX_FILE = KNOWLEDGE_DIR / "index.json"
CONDENSE_THRESHOLD_WORDS = 100_000


def _slugify(topic: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", topic.strip().lower()).strip("-")
    return slug[:80] or "topic"


def _ensure_dir() -> None:
    KNOWLEDGE_DIR.mkdir(parents=True, exist_ok=True)
    if not INDEX_FILE.exists():
        INDEX_FILE.write_text("{}", encoding="utf-8")


def _load_index() -> dict:
    _ensure_dir()
    try:
        return json.loads(INDEX_FILE.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return {}


def _save_index(index: dict) -> None:
    INDEX_FILE.write_text(json.dumps(index, indent=2), encoding="utf-8")


def _topic_words(topic: str) -> set[str]:
    return set(re.findall(r"[a-z0-9]+", topic.lower()))


def find_topic(query: str) -> str | None:
    """Best-effort match against saved topic titles - exact match first, then
    whichever existing topic shares the most words with the query."""
    index = _load_index()
    if not index:
        return None

    query_lower = query.strip().lower()
    for slug, title in index.items():
        if title.strip().lower() == query_lower:
            return slug

    query_words = _topic_words(query)
    if not query_words:
        return None

    best_slug, best_overlap = None, 0
    for slug, title in index.items():
        overlap = len(query_words & _topic_words(title))
        if overlap > best_overlap:
            best_slug, best_overlap = slug, overlap

    return best_slug if best_overlap >= 2 else None


def get_content(slug: str) -> str | None:
    path = KNOWLEDGE_DIR / f"{slug}.md"
    if not path.exists():
        return None
    return path.read_text(encoding="utf-8")


def save_topic(topic: str, content: str) -> str:
    _ensure_dir()
    slug = _slugify(topic)
    (KNOWLEDGE_DIR / f"{slug}.md").write_text(content, encoding="utf-8")

    index = _load_index()
    index[slug] = topic
    _save_index(index)
    return slug


def total_word_count() -> int:
    _ensure_dir()
    total = 0
    for path in KNOWLEDGE_DIR.glob("*.md"):
        total += len(path.read_text(encoding="utf-8").split())
    return total


def all_topic_files() -> list[Path]:
    _ensure_dir()
    return list(KNOWLEDGE_DIR.glob("*.md"))


def needs_condensing() -> bool:
    return total_word_count() > CONDENSE_THRESHOLD_WORDS
