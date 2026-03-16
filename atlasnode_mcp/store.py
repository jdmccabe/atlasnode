from __future__ import annotations

from array import array
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import UTC, datetime
import hashlib
import json
import importlib
import math
import os
from pathlib import Path
import re
import sqlite3
from threading import Lock
from typing import Any, Iterable

import httpx

try:
    import winreg
except ImportError:  # pragma: no cover - winreg is only available on Windows
    winreg = None


HASH_VECTOR_DIMENSION = 384
DEFAULT_EMBEDDING_BACKEND = "bge-m3"
DEFAULT_BGE_M3_MODEL = "BAAI/bge-m3"
DEFAULT_OPENAI_EMBEDDING_MODEL = "text-embedding-3-small"
OPENAI_EMBEDDINGS_URL = os.getenv("ATLASNODE_EMBEDDINGS_URL", "https://api.openai.com/v1/embeddings")
HASH_EMBEDDING_SIGNATURE = "local-hash-v1"
DEFAULT_CHUNK_WORDS = 180
DEFAULT_CHUNK_OVERLAP_WORDS = 40
DEFAULT_ANALYTICS_DAYS = 14
ANALYTICS_STOPWORDS = {
    "about",
    "after",
    "agent",
    "assistant",
    "before",
    "being",
    "brain",
    "build",
    "data",
    "document",
    "file",
    "from",
    "have",
    "into",
    "local",
    "memory",
    "AtlasNode",
    "mode",
    "name",
    "note",
    "path",
    "preferred",
    "protocol",
    "runtime",
    "should",
    "state",
    "stored",
    "system",
    "task",
    "that",
    "their",
    "this",
    "title",
    "update",
    "user",
    "using",
    "with",
}

SLIDER_DEFAULTS = {
    "verbosity": 28,
    "humor": 45,
    "creativity": 55,
    "morality": 60,
    "directness": 65,
    "technicality": 50,
}

MODE_PRESETS = {
    "base": {},
    "research": {"technicality": 85, "directness": 75, "humor": 25},
    "creative": {"creativity": 90, "humor": 70, "verbosity": 60},
    "technical": {"technicality": 90, "directness": 80, "creativity": 60, "humor": 35},
    "concise": {"verbosity": 15, "directness": 85},
}

MODE_TO_PERSONALITY_ID = {
    "base": "mode/base",
    "research": "mode/research",
    "creative": "mode/creative",
    "technical": "mode/technical",
    "concise": "mode/concise",
}

SLIDER_ALIASES = {
    "verbosity_boost": "verbosity",
    "humor_amp": "humor",
    "creativity_pulse": "creativity",
    "morality_compass": "morality",
    "directness_filter": "directness",
    "tech_depth": "technicality",
}

LEGACY_DOCUMENT_ALIASES = {
    "brain/MASTER_SPEC.md": "system/core",
    "brain/README.md": "system/overview",
    "brain/COMPATIBILITY.md": "protocol/compatibility",
    "brain/sliders/USER.md": "profile/defaults",
    "brain/personalities/BASE.md": "mode/base",
    "brain/personalities/RESEARCH_ANALYST.md": "mode/research",
    "brain/personalities/CREATIVE_DIRECTOR.md": "mode/creative",
    "brain/personalities/TECHNICAL_COPILOT.md": "mode/technical",
    "brain/gauges/LIVEHUD.md": "protocol/output",
    "brain/memory/MEMORY_PROTOCOL.md": "protocol/memory",
}

FACT_CATEGORY_ALIASES = {
    "general": "general",
    "fact": "general",
    "facts": "general",
    "misc": "general",
    "other": "general",
    "preference": "preference",
    "preferences": "preference",
    "pref": "preference",
    "project_detail": "project_detail",
    "project_details": "project_detail",
    "project": "project_detail",
    "project_detail_note": "project_detail",
    "project detail": "project_detail",
    "project details": "project_detail",
    "user_bio": "user_bio",
    "user bio": "user_bio",
    "bio": "user_bio",
    "identity": "user_bio",
    "profile": "user_bio",
    "workflow": "workflow",
    "workflow_preference": "workflow",
    "workflow preference": "workflow",
    "process": "workflow",
    "technical": "technical",
    "tech": "technical",
    "implementation": "technical",
    "engineering": "technical",
}

MEMORY_SCOPE_ALIASES = {
    "global": "global",
    "shared": "global",
    "default": "global",
    "workspace": "workspace",
    "project": "workspace",
    "repo": "workspace",
    "thread": "thread",
    "session": "thread",
    "chat": "thread",
    "user": "user",
    "profile": "user",
}

LOW_VALUE_FACT_PATTERNS = (
    re.compile(r"^(hi|hello|hey|thanks|thank you|ok|okay|sounds good|got it|cool)[!. ]*$", re.IGNORECASE),
    re.compile(r"^(test|testing)[!. ]*$", re.IGNORECASE),
)

_BGE_M3_MODEL: Any | None = None
_BGE_M3_MODEL_LOCK = Lock()


@dataclass(frozen=True)
class SeedDocument:
    doc_id: str
    doc_type: str
    title: str
    content: str
    source: str = "seed"


SEED_DOCUMENTS = (
    SeedDocument(
        doc_id="system/core",
        doc_type="system",
        title="AtlasNode Core Runtime",
        content=(
            "AtlasNode is a stateful AI runtime optimized for software work. The source of truth is the "
            "vector-backed database and the live runtime state, not loose files. Favor direct answers, "
            "tool use, verification, and efficient context retrieval over ceremony or decorative prompts."
        ),
    ),
    SeedDocument(
        doc_id="system/overview",
        doc_type="system",
        title="AtlasNode Overview",
        content=(
            "AtlasNode stores runtime state, instruction documents, and durable memory in a single local "
            "database. Prompt assembly is retrieval-driven: load current state, pull the active mode "
            "overlay, retrieve the most relevant operational documents, then retrieve the most relevant "
            "memory records for the task."
        ),
    ),
    SeedDocument(
        doc_id="protocol/retrieval",
        doc_type="protocol",
        title="Retrieval Protocol",
        content=(
            "Before substantial work, read the current runtime state. Use a think-before-you-act retrieval gate: "
            "first ask whether the current request can be answered from the active chat and runtime state alone. "
            "Only query durable memory or episodic history when the task references prior work, user preferences, "
            "project status, vague follow-up context, or missing facts that are likely stored. Build prompts from "
            "the active mode, core operating documents, and only the task-relevant supporting context returned by retrieval."
        ),
    ),
    SeedDocument(
        doc_id="protocol/memory",
        doc_type="protocol",
        title="Memory Protocol",
        content=(
            "Persist durable user preferences, identity facts, corrections, project milestones, and high "
            "value technical learnings as semantic memory records. Prefer one concept per memory. Update an "
            "existing memory when it changes rather than creating near-duplicates. Do not store secrets or "
            "credentials in memory."
        ),
    ),
    SeedDocument(
        doc_id="protocol/episodes",
        doc_type="protocol",
        title="Episodic History Protocol",
        content=(
            "Keep episodic history separate from semantic memory. Episodic records capture time-ordered summaries "
            "of what happened, such as decisions, milestones, or session outcomes. Use episodic history for questions "
            "like what changed recently, what we did yesterday, or where work left off."
        ),
    ),
    SeedDocument(
        doc_id="protocol/verification",
        doc_type="protocol",
        title="Verification Protocol",
        content=(
            "If an action is claimed, verify it. Inspect repository state before editing, run relevant "
            "checks after changes, and keep uncertainty explicit. Do not report work as complete without "
            "validation when validation is available."
        ),
    ),
    SeedDocument(
        doc_id="protocol/compatibility",
        doc_type="protocol",
        title="Compatibility Protocol",
        content=(
            "When the host limits a capability, acknowledge the limitation briefly and continue with the "
            "best available fallback. Prefer local retrieval, local execution, and deterministic state "
            "updates over external services unless the task specifically requires them."
        ),
    ),
    SeedDocument(
        doc_id="protocol/output",
        doc_type="protocol",
        title="Output Protocol",
        content=(
            "Prefer concise, structured responses. Lead with the answer, then the most relevant reasoning "
            "or evidence. Avoid empty enthusiasm, padding, or needless repetition."
        ),
    ),
    SeedDocument(
        doc_id="profile/defaults",
        doc_type="profile",
        title="Default User Profile",
        content=(
            "Assume the user prefers direct, structured, technical output with minimal fluff. Offer a best "
            "next action when it is genuinely useful. Ask questions only when a material blocker cannot "
            "be resolved from local context."
        ),
    ),
    SeedDocument(
        doc_id="mode/base",
        doc_type="mode",
        title="Base Mode",
        content=(
            "Balanced operating mode. Stay useful, collaborative, and grounded. Use moderate technical "
            "depth and directness."
        ),
    ),
    SeedDocument(
        doc_id="mode/research",
        doc_type="mode",
        title="Research Mode",
        content=(
            "Optimize for evidence gathering, structured synthesis, and uncertainty labeling. Increase "
            "technical depth and analytical rigor. Reduce humor."
        ),
    ),
    SeedDocument(
        doc_id="mode/creative",
        doc_type="mode",
        title="Creative Mode",
        content=(
            "Optimize for ideation, exploration, and novel combinations while remaining coherent. Increase "
            "creativity and openness to unconventional options."
        ),
    ),
    SeedDocument(
        doc_id="mode/technical",
        doc_type="mode",
        title="Technical Mode",
        content=(
            "Optimize for implementation, debugging, automation, and architecture. Keep technicality and "
            "directness high, preserve some creativity, and use humor sparingly."
        ),
    ),
    SeedDocument(
        doc_id="mode/concise",
        doc_type="mode",
        title="Concise Mode",
        content=(
            "Optimize for short, high-signal answers. Minimize verbosity and favor direct action over "
            "extended exposition."
        ),
    ),
)


def _utc_now() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds")


def _normalize_mode_name(name: str) -> str:
    normalized = re.sub(r"[^a-zA-Z0-9]+", "_", name.strip().lower()).strip("_")
    normalized = normalized.replace("_mode", "")
    aliases = {
        "research_analyst": "research",
        "creative_director": "creative",
        "technical_copilot": "technical",
    }
    normalized = aliases.get(normalized, normalized)
    if normalized not in MODE_PRESETS:
        raise ValueError(f"Unknown mode '{name}'. Available: {sorted(MODE_PRESETS)}")
    return normalized


def _normalize_slider_name(name: str) -> str:
    normalized = re.sub(r"[^a-zA-Z0-9]+", "_", name.strip().lower()).strip("_")
    normalized = SLIDER_ALIASES.get(normalized, normalized)
    if normalized not in SLIDER_DEFAULTS:
        raise ValueError(f"Unknown slider '{name}'. Available: {sorted(SLIDER_DEFAULTS)}")
    return normalized


def _slugify(value: str) -> str:
    cleaned = re.sub(r"[^a-zA-Z0-9]+", "_", value.strip().lower()).strip("_")
    if not cleaned:
        raise ValueError("Value must contain letters or numbers.")
    return cleaned[:96]


def _normalize_fact_category(category: str) -> str:
    if not category or not category.strip():
        return "general"
    normalized = _slugify(category)
    mapped = FACT_CATEGORY_ALIASES.get(normalized)
    if mapped is None:
        allowed = ", ".join(sorted(set(FACT_CATEGORY_ALIASES.values())))
        raise ValueError(f"Unknown fact category '{category}'. Allowed categories: {allowed}")
    return mapped


def _normalize_memory_scope(scope: str | None) -> str:
    if scope is None or not scope.strip():
        return "global"
    normalized = _slugify(scope)
    mapped = MEMORY_SCOPE_ALIASES.get(normalized)
    if mapped is None:
        allowed = ", ".join(sorted(set(MEMORY_SCOPE_ALIASES.values())))
        raise ValueError(f"Unknown memory scope '{scope}'. Allowed scopes: {allowed}")
    return mapped


def _normalize_namespace(namespace: str | None) -> str | None:
    if namespace is None or not namespace.strip():
        return None
    return _slugify(namespace)


def _fact_key(name: str) -> str:
    return _slugify(name)


def _semantic_metadata(
    name: str,
    category: str,
    *,
    scope: str,
    namespace: str | None,
    existing: dict[str, Any] | None = None,
) -> dict[str, Any]:
    previous = existing or {}
    if previous.get("kind") == "semantic":
        version = int(previous.get("fact_version") or 0) + 1
    else:
        version = 1
    return {
        "kind": "semantic",
        "category": _normalize_fact_category(category),
        "fact_key": _fact_key(name),
        "fact_status": "active",
        "fact_version": version,
        "scope": _normalize_memory_scope(scope),
        "namespace": _normalize_namespace(namespace),
    }


def _is_superseded_semantic(metadata: dict[str, Any]) -> bool:
    return metadata.get("kind") == "semantic" and metadata.get("fact_status") == "superseded"


def _memory_metadata(kind: str, *, scope: str, namespace: str | None, **extra: Any) -> dict[str, Any]:
    payload = {
        "kind": kind,
        "scope": _normalize_memory_scope(scope),
        "namespace": _normalize_namespace(namespace),
    }
    payload.update(extra)
    return payload


def _metadata_scope_rank(
    metadata: dict[str, Any],
    *,
    scope: str | None = None,
    namespace: str | None = None,
    include_global: bool = True,
) -> int:
    if scope is None:
        return 1
    target_scope = _normalize_memory_scope(scope)
    target_namespace = _normalize_namespace(namespace)
    item_scope = _normalize_memory_scope(str(metadata.get("scope") or "global"))
    item_namespace = _normalize_namespace(metadata.get("namespace"))
    if item_scope == target_scope and item_namespace == target_namespace:
        return 3
    if include_global and item_scope == "global":
        return 1
    return 0


def _is_low_value_fact(name: str, content: str, category: str) -> str | None:
    normalized_content = " ".join(content.strip().split())
    if not normalized_content:
        return "content is empty"
    if any(pattern.match(normalized_content) for pattern in LOW_VALUE_FACT_PATTERNS):
        return "content looks like low-value chatter"
    token_count = len(_tokenize(normalized_content))
    if category == "general" and token_count < 4:
        return "general facts need more specific detail"
    if len(normalized_content) < 16 and token_count < 4:
        return "fact is too short to be reliably useful later"
    if _slugify(name) == _slugify(normalized_content) and token_count < 6:
        return "fact duplicates its title without enough detail"
    return None


def _tokenize(text: str) -> list[str]:
    lowered = text.lower()
    return re.findall(r"[a-z0-9_]+", lowered)


def _stable_hash(value: str, digest_size: int = 16) -> bytes:
    return hashlib.blake2b(value.encode("utf-8"), digest_size=digest_size).digest()


def _hash_embed_text(text: str) -> array:
    vector = [0.0] * HASH_VECTOR_DIMENSION
    tokens = _tokenize(text)
    if not tokens:
        return array("f", vector)

    for token in tokens:
        digest = _stable_hash(f"tok:{token}")
        index = int.from_bytes(digest[:8], "big") % HASH_VECTOR_DIMENSION
        sign = 1.0 if digest[8] % 2 == 0 else -1.0
        vector[index] += sign

    if len(tokens) > 1:
        for left, right in zip(tokens, tokens[1:]):
            digest = _stable_hash(f"bg:{left}|{right}")
            index = int.from_bytes(digest[:8], "big") % HASH_VECTOR_DIMENSION
            sign = 0.7 if digest[8] % 2 == 0 else -0.7
            vector[index] += sign

    joined = re.sub(r"\s+", "", text.lower())
    if len(joined) >= 5:
        for start in range(0, min(len(joined) - 2, 512)):
            trigram = joined[start : start + 3]
            digest = _stable_hash(f"cg:{trigram}")
            index = int.from_bytes(digest[:8], "big") % HASH_VECTOR_DIMENSION
            sign = 0.15 if digest[8] % 2 == 0 else -0.15
            vector[index] += sign

    norm = math.sqrt(sum(value * value for value in vector))
    if norm:
        vector = [value / norm for value in vector]
    return array("f", vector)


def _read_windows_environment_value(name: str) -> str | None:
    if os.name != "nt" or winreg is None:
        return None

    locations = (
        (winreg.HKEY_CURRENT_USER, r"Environment"),
        (winreg.HKEY_LOCAL_MACHINE, r"SYSTEM\CurrentControlSet\Control\Session Manager\Environment"),
    )
    for hive, subkey in locations:
        try:
            with winreg.OpenKey(hive, subkey) as key:
                value, _ = winreg.QueryValueEx(key, name)
        except OSError:
            continue
        if isinstance(value, str) and value:
            return os.path.expandvars(value)
    return None


def _environment_value(name: str) -> str | None:
    value = os.getenv(name)
    if value:
        return value

    value = _read_windows_environment_value(name)
    if value:
        os.environ[name] = value
    return value


def _embedding_backend() -> str:
    backend = os.getenv("ATLASNODE_EMBEDDING_BACKEND", DEFAULT_EMBEDDING_BACKEND).strip().lower()
    aliases = {
        "default": "bge-m3",
        "bge": "bge-m3",
        "baai": "bge-m3",
        "local": "bge-m3",
        "openai": "openai",
        "hash": "hash",
        "local-hash": "hash",
    }
    normalized = aliases.get(backend, backend)
    if normalized not in {"bge-m3", "openai", "hash"}:
        raise ValueError(
            "ATLASNODE_EMBEDDING_BACKEND must be one of: bge-m3, openai, hash."
        )
    return normalized


def _bge_m3_model_name() -> str:
    explicit = os.getenv("ATLASNODE_BGE_M3_MODEL")
    if explicit:
        return explicit.strip() or DEFAULT_BGE_M3_MODEL

    legacy = os.getenv("ATLASNODE_EMBEDDING_MODEL")
    if legacy and os.getenv("ATLASNODE_EMBEDDING_BACKEND", "").strip().lower() in {"bge", "bge-m3", "baai", "local"}:
        return legacy.strip() or DEFAULT_BGE_M3_MODEL

    return DEFAULT_BGE_M3_MODEL


def _bge_m3_model_path() -> str | None:
    raw = _environment_value("ATLASNODE_EMBEDDING_MODEL_PATH")
    if not raw:
        return None
    return str(Path(raw).expanduser())


def _bge_m3_model_source() -> str:
    return _bge_m3_model_path() or _bge_m3_model_name()


def _load_bge_m3_model() -> Any:
    global _BGE_M3_MODEL

    if _BGE_M3_MODEL is not None:
        return _BGE_M3_MODEL

    with _BGE_M3_MODEL_LOCK:
        if _BGE_M3_MODEL is not None:
            return _BGE_M3_MODEL

        try:
            sentence_transformers = importlib.import_module("sentence_transformers")
        except ModuleNotFoundError as exc:  # pragma: no cover - exercised via runtime failure path
            raise RuntimeError(
                "sentence-transformers is required for ATLASNODE_EMBEDDING_BACKEND=bge-m3. "
                "Install AtlasNode dependencies with `python -m pip install -e .`."
            ) from exc

        _BGE_M3_MODEL = sentence_transformers.SentenceTransformer(
            _bge_m3_model_source(),
            trust_remote_code=True,
        )
        max_length = os.getenv("ATLASNODE_EMBEDDING_MAX_LENGTH")
        if max_length:
            _BGE_M3_MODEL.max_seq_length = max(128, int(max_length))
        return _BGE_M3_MODEL


def _active_embedding_signature() -> str:
    backend = _embedding_backend()
    if backend == "bge-m3":
        return f"bge-m3:{_bge_m3_model_source()}"
    if backend == "openai":
        api_key = _environment_value("OPENAI_API_KEY")
        if not api_key:
            raise RuntimeError("OPENAI_API_KEY is required for ATLASNODE_EMBEDDING_BACKEND=openai.")
        dimensions = os.getenv("ATLASNODE_EMBEDDING_DIMENSIONS")
        dimension_suffix = f":{dimensions}" if dimensions else ""
        model = os.getenv("ATLASNODE_OPENAI_EMBEDDING_MODEL", os.getenv("ATLASNODE_EMBEDDING_MODEL", DEFAULT_OPENAI_EMBEDDING_MODEL))
        return f"openai:{model}{dimension_suffix}"
    return HASH_EMBEDDING_SIGNATURE


def _openai_embed_texts(texts: list[str]) -> list[array]:
    api_key = _environment_value("OPENAI_API_KEY")
    if not api_key:
        raise RuntimeError("OPENAI_API_KEY is required for OpenAI embeddings.")

    model = os.getenv("ATLASNODE_OPENAI_EMBEDDING_MODEL", os.getenv("ATLASNODE_EMBEDDING_MODEL", DEFAULT_OPENAI_EMBEDDING_MODEL))
    payload: dict[str, Any] = {
        "input": texts,
        "model": model,
    }
    dimensions = os.getenv("ATLASNODE_EMBEDDING_DIMENSIONS")
    if dimensions:
        payload["dimensions"] = int(dimensions)

    response = httpx.post(
        OPENAI_EMBEDDINGS_URL,
        headers={
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
        },
        json=payload,
        timeout=60.0,
    )
    response.raise_for_status()
    payload = response.json()
    return [array("f", item["embedding"]) for item in sorted(payload["data"], key=lambda item: item["index"])]


def _bge_m3_embed_texts(texts: list[str]) -> list[array]:
    model = _load_bge_m3_model()
    batch_size = max(1, int(os.getenv("ATLASNODE_EMBEDDING_BATCH_SIZE", "8")))
    encoded = model.encode(
        texts,
        batch_size=batch_size,
        convert_to_numpy=True,
        normalize_embeddings=True,
        show_progress_bar=False,
    )
    return [array("f", vector.tolist()) for vector in encoded]


def _embed_texts(texts: list[str]) -> list[array]:
    backend = _embedding_backend()
    if backend == "bge-m3":
        return _bge_m3_embed_texts(texts)
    if backend == "openai":
        return _openai_embed_texts(texts)
    return [_hash_embed_text(text) for text in texts]


def _embed_text(text: str) -> array:
    return _embed_texts([text])[0]


def _chunk_words() -> int:
    return max(32, int(os.getenv("ATLASNODE_CHUNK_WORDS", str(DEFAULT_CHUNK_WORDS))))


def _chunk_overlap_words() -> int:
    overlap = int(os.getenv("ATLASNODE_CHUNK_OVERLAP_WORDS", str(DEFAULT_CHUNK_OVERLAP_WORDS)))
    return max(0, min(overlap, _chunk_words() - 8))


def _chunk_signature() -> str:
    return f"chunk:{_chunk_words()}:{_chunk_overlap_words()}"


def _split_into_chunks(title: str, content: str) -> list[str]:
    words = re.findall(r"\S+", content.strip())
    if not words:
        return [title.strip()]

    size = _chunk_words()
    overlap = _chunk_overlap_words()
    step = max(1, size - overlap)
    chunks: list[str] = []
    for start in range(0, len(words), step):
        slice_words = words[start : start + size]
        if not slice_words:
            break
        chunk_body = " ".join(slice_words).strip()
        chunks.append(f"{title.strip()}\n{chunk_body}")
        if start + size >= len(words):
            break
    return chunks or [title.strip()]


def _embedding_to_bytes(embedding: array) -> bytes:
    return embedding.tobytes()


def _embedding_from_bytes(blob: bytes) -> array:
    values = array("f")
    values.frombytes(blob)
    return values


def _dot(left: array, right: array) -> float:
    return sum(l * r for l, r in zip(left, right))


def _excerpt(content: str, query: str, max_length: int = 240) -> str:
    lowered = content.lower()
    position = 0
    for token in _tokenize(query):
        found = lowered.find(token)
        if found != -1:
            position = found
            break
    start = max(0, position - 60)
    end = min(len(content), start + max_length)
    snippet = content[start:end].replace("\n", " ").strip()
    if start > 0:
        snippet = "..." + snippet
    if end < len(content):
        snippet += "..."
    return snippet


def _byte_length(value: str) -> int:
    return len(value.encode("utf-8"))


def _date_only(iso_timestamp: str) -> str:
    return iso_timestamp[:10]


MEMORY_GATE_PATTERNS = (
    "last time",
    "previously",
    "before",
    "earlier",
    "yesterday",
    "recently",
    "we did",
    "we were",
    "resume",
    "continue",
    "pick up",
    "next step",
    "my project",
    "my preference",
    "my preferences",
    "my setup",
    "our discussion",
    "our plan",
    "what did we",
    "where did we",
)


def _memory_gate_reason(task: str | None) -> str | None:
    if not task:
        return None
    normalized = " ".join(task.lower().split())
    for pattern in MEMORY_GATE_PATTERNS:
        if pattern in normalized:
            return f"task references prior context ({pattern})"
    if any(token in normalized for token in ("preference", "prefer", "remember", "history", "status", "decision")):
        return "task appears to depend on stored user or project context"
    return None


class AtlasNodeStore:
    def __init__(self, repo_root: Path | None = None, data_root: Path | None = None) -> None:
        self.repo_root = repo_root or Path(__file__).resolve().parent.parent
        self.data_root = data_root or (self.repo_root / ".atlasnode")
        self.db_path = self.data_root / "atlasnode.sqlite3"
        self._initialize()

    @contextmanager
    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.db_path)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA journal_mode=WAL;")
        connection.execute("PRAGMA synchronous=NORMAL;")
        try:
            yield connection
            connection.commit()
        finally:
            connection.close()

    def _initialize(self) -> None:
        self.data_root.mkdir(parents=True, exist_ok=True)
        with self._connect() as connection:
            connection.executescript(
                """
                CREATE TABLE IF NOT EXISTS documents (
                    doc_id TEXT PRIMARY KEY,
                    doc_type TEXT NOT NULL,
                    title TEXT NOT NULL,
                    content TEXT NOT NULL,
                    source TEXT NOT NULL,
                    metadata_json TEXT NOT NULL DEFAULT '{}',
                    embedding BLOB NOT NULL,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );

                CREATE INDEX IF NOT EXISTS idx_documents_type ON documents(doc_type);
                CREATE INDEX IF NOT EXISTS idx_documents_updated ON documents(updated_at DESC);

                CREATE VIRTUAL TABLE IF NOT EXISTS documents_fts USING fts5(
                    doc_id UNINDEXED,
                    title,
                    content,
                    tokenize='unicode61'
                );

                CREATE TABLE IF NOT EXISTS document_chunks (
                    chunk_id TEXT PRIMARY KEY,
                    doc_id TEXT NOT NULL,
                    chunk_index INTEGER NOT NULL,
                    content TEXT NOT NULL,
                    source_hash TEXT NOT NULL,
                    embedding_signature TEXT NOT NULL,
                    embedding BLOB NOT NULL,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    FOREIGN KEY(doc_id) REFERENCES documents(doc_id) ON DELETE CASCADE
                );

                CREATE INDEX IF NOT EXISTS idx_document_chunks_doc ON document_chunks(doc_id);
                CREATE INDEX IF NOT EXISTS idx_document_chunks_signature ON document_chunks(embedding_signature);

                CREATE VIRTUAL TABLE IF NOT EXISTS document_chunks_fts USING fts5(
                    chunk_id UNINDEXED,
                    doc_id UNINDEXED,
                    content,
                    tokenize='unicode61'
                );

                CREATE TABLE IF NOT EXISTS runtime_state (
                    singleton INTEGER PRIMARY KEY CHECK (singleton = 1),
                    mode TEXT NOT NULL,
                    sliders_json TEXT NOT NULL,
                    focus_json TEXT NOT NULL,
                    system_json TEXT NOT NULL,
                    session TEXT NOT NULL,
                    pending_writes INTEGER NOT NULL,
                    updated_at TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS metadata (
                    key TEXT PRIMARY KEY,
                    value TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS usage_events (
                    event_id INTEGER PRIMARY KEY AUTOINCREMENT,
                    event_type TEXT NOT NULL,
                    doc_id TEXT,
                    doc_type TEXT,
                    byte_count INTEGER NOT NULL DEFAULT 0,
                    metadata_json TEXT NOT NULL DEFAULT '{}',
                    occurred_at TEXT NOT NULL
                );

                CREATE INDEX IF NOT EXISTS idx_usage_events_type ON usage_events(event_type);
                CREATE INDEX IF NOT EXISTS idx_usage_events_occurred_at ON usage_events(occurred_at DESC);
                """
            )

            state_row = connection.execute("SELECT singleton FROM runtime_state WHERE singleton = 1").fetchone()
            if state_row is None:
                state = self.default_state()
                connection.execute(
                    """
                    INSERT INTO runtime_state (
                        singleton, mode, sliders_json, focus_json, system_json, session, pending_writes, updated_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        1,
                        state["mode"],
                        json.dumps(state["sliders"], sort_keys=True),
                        json.dumps(state["focus"], sort_keys=True),
                        json.dumps(state["system"], sort_keys=True),
                        state["session"],
                        state["pending_writes"],
                        _utc_now(),
                    ),
                )

            document_count = connection.execute("SELECT COUNT(*) AS count FROM documents").fetchone()["count"]
            if document_count == 0:
                self._sync_seed_documents(connection)
                self._set_metadata(connection, "embedding_signature", _active_embedding_signature())
                self._set_metadata(connection, "chunk_signature", _chunk_signature())
            else:
                self._sync_seed_documents(connection)
                self._sync_embeddings(connection)

    def _sync_seed_documents(self, connection: sqlite3.Connection) -> None:
        for document in SEED_DOCUMENTS:
            self._upsert_document(
                connection=connection,
                doc_id=document.doc_id,
                doc_type=document.doc_type,
                title=document.title,
                content=document.content,
                source=document.source,
                metadata={},
                track_write_event=False,
            )

    def default_state(self) -> dict[str, Any]:
        default_sliders = dict(SLIDER_DEFAULTS)
        default_sliders.update(MODE_PRESETS["technical"])
        return {
            "mode": "technical",
            "sliders": default_sliders,
            "focus": {
                "past": "No memory loaded yet",
                "present": "Software development by default",
                "future": "Build the next useful thing",
            },
            "system": {
                "context": "Stable",
                "tools": "Active",
                "vibe": "Direct",
            },
            "session": "new",
            "pending_writes": 0,
        }

    def _row_to_state(self, row: sqlite3.Row) -> dict[str, Any]:
        return {
            "mode": row["mode"],
            "sliders": json.loads(row["sliders_json"]),
            "focus": json.loads(row["focus_json"]),
            "system": json.loads(row["system_json"]),
            "session": row["session"],
            "pending_writes": row["pending_writes"],
        }

    def load_state(self) -> dict[str, Any]:
        with self._connect() as connection:
            row = connection.execute("SELECT * FROM runtime_state WHERE singleton = 1").fetchone()
            if row is None:
                return self.default_state()
            return self._row_to_state(row)

    def _save_state(self, connection: sqlite3.Connection, state: dict[str, Any]) -> dict[str, Any]:
        state["session"] = "active"
        connection.execute(
            """
            UPDATE runtime_state
            SET mode = ?, sliders_json = ?, focus_json = ?, system_json = ?, session = ?, pending_writes = ?, updated_at = ?
            WHERE singleton = 1
            """,
            (
                state["mode"],
                json.dumps(state["sliders"], sort_keys=True),
                json.dumps(state["focus"], sort_keys=True),
                json.dumps(state["system"], sort_keys=True),
                state["session"],
                state["pending_writes"],
                _utc_now(),
            ),
        )
        return state

    def _memory_count(self, connection: sqlite3.Connection) -> int:
        return connection.execute(
            "SELECT COUNT(*) AS count FROM documents WHERE doc_type = 'memory'"
        ).fetchone()["count"]

    def _record_usage_event(
        self,
        connection: sqlite3.Connection,
        event_type: str,
        byte_count: int,
        *,
        doc_id: str | None = None,
        doc_type: str | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> None:
        connection.execute(
            """
            INSERT INTO usage_events (event_type, doc_id, doc_type, byte_count, metadata_json, occurred_at)
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            (
                event_type,
                doc_id,
                doc_type,
                max(0, int(byte_count)),
                json.dumps(metadata or {}, sort_keys=True),
                _utc_now(),
            ),
        )

    def state_response(self) -> dict[str, Any]:
        with self._connect() as connection:
            state = self.load_state()
            response = json.loads(json.dumps(state))
            storage_path = str(self.db_path.relative_to(self.repo_root)).replace("\\", "/")
            response["memory"] = {
                "file_count": self._memory_count(connection),
                "store_path": storage_path,
                "state_file": storage_path,
            }
            response["personality_file"] = MODE_TO_PERSONALITY_ID[response["mode"]]
            response["storage_backend"] = "sqlite-hybrid-vector"
            response["embedding_backend"] = _active_embedding_signature()
            return response

    def _upsert_document(
        self,
        connection: sqlite3.Connection,
        doc_id: str,
        doc_type: str,
        title: str,
        content: str,
        source: str,
        metadata: dict[str, Any],
        track_write_event: bool = True,
    ) -> None:
        now = _utc_now()
        embedding = _embedding_to_bytes(_embed_text(f"{title}\n{content}"))
        existing = connection.execute(
            "SELECT created_at, title, content, doc_type FROM documents WHERE doc_id = ?",
            (doc_id,),
        ).fetchone()
        created_at = existing["created_at"] if existing is not None else now
        previous_bytes = 0
        if existing is not None:
            previous_bytes = _byte_length(existing["title"]) + _byte_length(existing["content"])
        current_bytes = _byte_length(title.strip()) + _byte_length(content.strip())
        connection.execute(
            """
            INSERT INTO documents (doc_id, doc_type, title, content, source, metadata_json, embedding, created_at, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(doc_id) DO UPDATE SET
                doc_type = excluded.doc_type,
                title = excluded.title,
                content = excluded.content,
                source = excluded.source,
                metadata_json = excluded.metadata_json,
                embedding = excluded.embedding,
                updated_at = excluded.updated_at
            """,
            (
                doc_id,
                doc_type,
                title.strip(),
                content.strip(),
                source,
                json.dumps(metadata, sort_keys=True),
                embedding,
                created_at,
                now,
            ),
        )
        connection.execute("DELETE FROM documents_fts WHERE doc_id = ?", (doc_id,))
        connection.execute(
            "INSERT INTO documents_fts (doc_id, title, content) VALUES (?, ?, ?)",
            (doc_id, title.strip(), content.strip()),
        )
        self._sync_document_chunks(
            connection=connection,
            doc_id=doc_id,
            title=title.strip(),
            content=content.strip(),
        )
        if track_write_event and source != "seed":
            self._record_usage_event(
                connection,
                "write",
                max(current_bytes - previous_bytes, 0),
                doc_id=doc_id,
                doc_type=doc_type,
                metadata={
                    "source": source,
                    "created": existing is None,
                },
            )

    def _metadata_value(self, connection: sqlite3.Connection, key: str) -> str | None:
        row = connection.execute("SELECT value FROM metadata WHERE key = ?", (key,)).fetchone()
        if row is None:
            return None
        return row["value"]

    def _set_metadata(self, connection: sqlite3.Connection, key: str, value: str) -> None:
        connection.execute(
            """
            INSERT INTO metadata (key, value)
            VALUES (?, ?)
            ON CONFLICT(key) DO UPDATE SET value = excluded.value
            """,
            (key, value),
        )

    def _sync_embeddings(self, connection: sqlite3.Connection) -> None:
        active_signature = _active_embedding_signature()
        stored_signature = self._metadata_value(connection, "embedding_signature")
        current_chunk_signature = _chunk_signature()
        stored_chunk_signature = self._metadata_value(connection, "chunk_signature")
        if stored_signature == active_signature and stored_chunk_signature == current_chunk_signature:
            return

        rows = connection.execute(
            "SELECT doc_id, title, content FROM documents ORDER BY doc_id"
        ).fetchall()
        for row in rows:
            self._sync_document_chunks(
                connection=connection,
                doc_id=row["doc_id"],
                title=row["title"],
                content=row["content"],
            )

        self._set_metadata(connection, "embedding_signature", active_signature)
        self._set_metadata(connection, "chunk_signature", current_chunk_signature)

    def _sync_document_chunks(
        self,
        connection: sqlite3.Connection,
        doc_id: str,
        title: str,
        content: str,
    ) -> None:
        chunk_texts = _split_into_chunks(title, content)
        source_hash = hashlib.sha256("\n\n".join(chunk_texts).encode("utf-8")).hexdigest()
        embedding_signature = _active_embedding_signature()

        existing = connection.execute(
            """
            SELECT chunk_id, chunk_index, source_hash, embedding_signature
            FROM document_chunks
            WHERE doc_id = ?
            ORDER BY chunk_index
            """,
            (doc_id,),
        ).fetchall()

        chunk_count_matches = len(existing) == len(chunk_texts)
        hashes_match = all(
            row["source_hash"] == source_hash and row["embedding_signature"] == embedding_signature
            for row in existing
        )
        if existing and chunk_count_matches and hashes_match:
            return

        embeddings = _embed_texts(chunk_texts)
        now = _utc_now()
        connection.execute("DELETE FROM document_chunks WHERE doc_id = ?", (doc_id,))
        connection.execute("DELETE FROM document_chunks_fts WHERE doc_id = ?", (doc_id,))

        document_embedding = embeddings[0]
        for index, (chunk_text, embedding) in enumerate(zip(chunk_texts, embeddings)):
            chunk_id = f"{doc_id}#chunk-{index}"
            connection.execute(
                """
                INSERT INTO document_chunks (
                    chunk_id, doc_id, chunk_index, content, source_hash, embedding_signature, embedding, created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    chunk_id,
                    doc_id,
                    index,
                    chunk_text,
                    source_hash,
                    embedding_signature,
                    _embedding_to_bytes(embedding),
                    now,
                    now,
                ),
            )
            connection.execute(
                "INSERT INTO document_chunks_fts (chunk_id, doc_id, content) VALUES (?, ?, ?)",
                (chunk_id, doc_id, chunk_text),
            )
            if index == 0:
                document_embedding = embedding

        connection.execute(
            "UPDATE documents SET embedding = ?, updated_at = ? WHERE doc_id = ?",
            (_embedding_to_bytes(document_embedding), now, doc_id),
        )

    def list_document_ids(self, include_memory: bool = False) -> list[str]:
        with self._connect() as connection:
            if include_memory:
                rows = connection.execute(
                    "SELECT doc_id FROM documents ORDER BY doc_type, doc_id"
                ).fetchall()
            else:
                rows = connection.execute(
                    "SELECT doc_id FROM documents WHERE doc_type != 'memory' ORDER BY doc_type, doc_id"
                ).fetchall()
            return [row["doc_id"] for row in rows]

    def read_document(self, identifier: str) -> dict[str, Any]:
        doc_id = LEGACY_DOCUMENT_ALIASES.get(identifier, identifier)
        with self._connect() as connection:
            row = connection.execute(
                """
                SELECT doc_id, doc_type, title, content, source, metadata_json, created_at, updated_at
                FROM documents
                WHERE doc_id = ?
                """,
                (doc_id,),
            ).fetchone()
            if row is None:
                raise FileNotFoundError(f"Document not found: {identifier}")
            metadata = json.loads(row["metadata_json"])
            document = {
                "id": row["doc_id"],
                "type": row["doc_type"],
                "title": row["title"],
                "content": row["content"],
                "source": row["source"],
                "metadata": metadata,
                "created_at": row["created_at"],
                "updated_at": row["updated_at"],
            }
            self._record_usage_event(
                connection,
                "read",
                _byte_length(document["title"]) + _byte_length(document["content"]),
                doc_id=document["id"],
                doc_type=document["type"],
                metadata={"identifier": identifier},
            )
            return document

    def _fts_scores(
        self,
        connection: sqlite3.Connection,
        query: str,
        doc_types: set[str],
        limit: int,
    ) -> dict[str, float]:
        tokens = _tokenize(query)
        if not tokens:
            return {}
        match_query = " OR ".join(dict.fromkeys(tokens))
        placeholders = ", ".join("?" for _ in doc_types)
        rows = connection.execute(
            f"""
            SELECT c.chunk_id, bm25(document_chunks_fts) AS rank
            FROM document_chunks_fts
            JOIN document_chunks AS c ON c.chunk_id = document_chunks_fts.chunk_id
            JOIN documents AS d ON d.doc_id = c.doc_id
            WHERE document_chunks_fts MATCH ?
              AND d.doc_type IN ({placeholders})
            ORDER BY rank
            LIMIT ?
            """,
            (match_query, *sorted(doc_types), limit),
        ).fetchall()
        scores: dict[str, float] = {}
        for row in rows:
            rank = abs(float(row["rank"]))
            scores[row["chunk_id"]] = 1.0 / (1.0 + rank)
        return scores

    def search_documents(
        self,
        query: str,
        doc_types: Iterable[str],
        limit: int = 10,
        *,
        scope: str | None = None,
        namespace: str | None = None,
        include_global: bool = True,
    ) -> list[dict[str, Any]]:
        normalized_query = query.strip()
        if not normalized_query:
            raise ValueError("Query must not be empty.")

        type_set = set(doc_types)
        if not type_set:
            return []

        query_embedding = _embed_text(normalized_query)
        with self._connect() as connection:
            lexical_scores = self._fts_scores(connection, normalized_query, type_set, max(limit * 10, 20))
            placeholders = ", ".join("?" for _ in type_set)
            rows = connection.execute(
                f"""
                SELECT
                    d.doc_id,
                    d.doc_type,
                    d.title,
                    d.metadata_json,
                    d.updated_at,
                    c.chunk_id,
                    c.content,
                    c.embedding
                FROM document_chunks AS c
                JOIN documents AS d ON d.doc_id = c.doc_id
                WHERE d.doc_type IN ({placeholders})
                """,
                tuple(sorted(type_set)),
            ).fetchall()

            aggregated: dict[str, dict[str, Any]] = {}
            for row in rows:
                metadata = json.loads(row["metadata_json"])
                if _is_superseded_semantic(metadata):
                    continue
                scope_rank = _metadata_scope_rank(
                    metadata,
                    scope=scope,
                    namespace=namespace,
                    include_global=include_global,
                )
                if scope_rank <= 0:
                    continue
                embedding = _embedding_from_bytes(row["embedding"])
                vector_score = max(_dot(query_embedding, embedding), 0.0)
                lexical_score = lexical_scores.get(row["chunk_id"], 0.0)
                combined = (vector_score * 0.78) + (lexical_score * 0.22)
                if combined <= 0:
                    continue

                current = aggregated.get(row["doc_id"])
                if current is None or combined > current["score"]:
                    aggregated[row["doc_id"]] = {
                        "id": row["doc_id"],
                        "type": row["doc_type"],
                        "title": row["title"],
                        "score": round(combined, 6),
                        "excerpt": _excerpt(row["content"], normalized_query),
                        "updated_at": row["updated_at"],
                        "metadata": metadata,
                        "scope_rank": scope_rank,
                    }

        matches = sorted(
            aggregated.values(),
            key=lambda item: (-item["scope_rank"], -item["score"], item["id"]),
        )
        limited_matches = matches[:limit]
        with self._connect() as connection:
            self._record_usage_event(
                connection,
                "search",
                sum(_byte_length(match["excerpt"]) for match in limited_matches),
                metadata={
                    "query": normalized_query,
                    "doc_types": sorted(type_set),
                    "limit": limit,
                    "result_count": len(limited_matches),
                    "scope": scope,
                    "namespace": _normalize_namespace(namespace),
                },
            )
        return limited_matches

    def list_memories(self) -> list[str]:
        with self._connect() as connection:
            rows = connection.execute(
                "SELECT doc_id FROM documents WHERE doc_type = 'memory' ORDER BY updated_at DESC, doc_id"
            ).fetchall()
            return [row["doc_id"] for row in rows]

    def list_episodes(self, limit: int = 50) -> list[str]:
        with self._connect() as connection:
            rows = connection.execute(
                """
                SELECT doc_id
                FROM documents
                WHERE doc_type = 'episode'
                ORDER BY updated_at DESC, doc_id
                LIMIT ?
                """,
                (max(1, limit),),
            ).fetchall()
            return [row["doc_id"] for row in rows]

    def latest_memories(
        self,
        limit: int,
        *,
        scope: str | None = None,
        namespace: str | None = None,
        include_global: bool = True,
    ) -> list[dict[str, Any]]:
        with self._connect() as connection:
            rows = connection.execute(
                """
                SELECT doc_id, title, content, metadata_json, created_at, updated_at
                FROM documents
                WHERE doc_type = 'memory'
                ORDER BY updated_at DESC, doc_id
                """,
            ).fetchall()
            memories: list[dict[str, Any]] = []
            for row in rows:
                metadata = json.loads(row["metadata_json"])
                if _is_superseded_semantic(metadata):
                    continue
                if _metadata_scope_rank(
                    metadata,
                    scope=scope,
                    namespace=namespace,
                    include_global=include_global,
                ) <= 0:
                    continue
                memories.append(
                    {
                        "id": row["doc_id"],
                        "title": row["title"],
                        "excerpt": _excerpt(row["content"], row["title"]),
                        "created_at": row["created_at"],
                        "updated_at": row["updated_at"],
                        "metadata": metadata,
                    }
                )
                if len(memories) >= max(1, limit):
                    break
            return memories

    def recent_episodes(
        self,
        limit: int = 8,
        days: int = 7,
        *,
        scope: str | None = None,
        namespace: str | None = None,
        include_global: bool = True,
    ) -> list[dict[str, Any]]:
        cutoff = datetime.now(UTC).replace(microsecond=0)
        cutoff = cutoff.timestamp() - (max(1, days) * 86400)
        cutoff_iso = datetime.fromtimestamp(cutoff, UTC).isoformat().replace("+00:00", "Z")
        with self._connect() as connection:
            rows = connection.execute(
                """
                SELECT doc_id, title, content, metadata_json, created_at, updated_at
                FROM documents
                WHERE doc_type = 'episode' AND updated_at >= ?
                ORDER BY updated_at DESC, doc_id
                LIMIT ?
                """,
                (cutoff_iso, max(1, limit)),
            ).fetchall()
            episodes: list[dict[str, Any]] = []
            for row in rows:
                metadata = json.loads(row["metadata_json"])
                if _metadata_scope_rank(
                    metadata,
                    scope=scope,
                    namespace=namespace,
                    include_global=include_global,
                ) <= 0:
                    continue
                episodes.append(
                    {
                        "id": row["doc_id"],
                        "title": row["title"],
                        "excerpt": _excerpt(row["content"], row["title"]),
                        "created_at": row["created_at"],
                        "updated_at": row["updated_at"],
                        "metadata": metadata,
                    }
                )
                if len(episodes) >= max(1, limit):
                    break
            return episodes

    def write_memory(
        self,
        name: str,
        content: str,
        overwrite: bool = False,
        *,
        scope: str = "global",
        namespace: str | None = None,
    ) -> str:
        memory_id = f"memory/{_slugify(name)}"
        with self._connect() as connection:
            existing = connection.execute(
                "SELECT doc_id FROM documents WHERE doc_id = ?",
                (memory_id,),
            ).fetchone()
            if existing is not None and not overwrite:
                raise FileExistsError(f"Memory already exists: {memory_id}")

            self._upsert_document(
                connection=connection,
                doc_id=memory_id,
                doc_type="memory",
                title=name.strip(),
                content=content,
                source="memory",
                metadata=_memory_metadata("memory", scope=scope, namespace=namespace),
            )

            state = self._row_to_state(
                connection.execute("SELECT * FROM runtime_state WHERE singleton = 1").fetchone()
            )
            state["pending_writes"] = 0
            state["focus"]["past"] = f"Stored memory {memory_id}"
            self._save_state(connection, state)
            return memory_id

    def remember_fact(
        self,
        name: str,
        content: str,
        *,
        category: str = "general",
        overwrite: bool = True,
        scope: str = "global",
        namespace: str | None = None,
    ) -> str:
        fact_key = _fact_key(name)
        normalized_category = _normalize_fact_category(category)
        normalized_scope = _normalize_memory_scope(scope)
        normalized_namespace = _normalize_namespace(namespace)
        rejection_reason = _is_low_value_fact(name, content, normalized_category)
        if rejection_reason is not None:
            raise ValueError(f"Fact was not stored: {rejection_reason}.")
        memory_id = f"memory/{fact_key}"
        with self._connect() as connection:
            existing = connection.execute(
                "SELECT doc_id, content, metadata_json FROM documents WHERE doc_id = ?",
                (memory_id,),
            ).fetchone()
            if existing is not None and not overwrite:
                raise FileExistsError(f"Memory already exists: {memory_id}")
            existing_metadata = json.loads(existing["metadata_json"]) if existing is not None else None
            if existing is not None:
                existing_scope = _normalize_memory_scope(str(existing_metadata.get("scope") or "global"))
                existing_namespace = _normalize_namespace(existing_metadata.get("namespace"))
                if existing_scope != normalized_scope or existing_namespace != normalized_namespace:
                    raise ValueError(
                        f"Memory topic '{name}' already exists in scope '{existing_scope}'"
                        f"{':' + existing_namespace if existing_namespace else ''}."
                    )
                if existing["content"].strip() == content.strip() and existing_metadata.get("category") == normalized_category:
                    return memory_id

            self._upsert_document(
                connection=connection,
                doc_id=memory_id,
                doc_type="memory",
                title=name.strip(),
                content=content,
                source="memory",
                metadata=_semantic_metadata(
                    name,
                    normalized_category,
                    scope=normalized_scope,
                    namespace=normalized_namespace,
                    existing=existing_metadata,
                ),
            )

            state = self._row_to_state(
                connection.execute("SELECT * FROM runtime_state WHERE singleton = 1").fetchone()
            )
            state["pending_writes"] = 0
            state["focus"]["past"] = f"Stored semantic memory {memory_id}"
            self._save_state(connection, state)
            return memory_id

    def append_memory(
        self,
        name: str,
        content: str,
        *,
        scope: str = "global",
        namespace: str | None = None,
    ) -> str:
        memory_id = f"memory/{_slugify(name)}"
        with self._connect() as connection:
            existing = connection.execute(
                "SELECT content, title, metadata_json FROM documents WHERE doc_id = ?",
                (memory_id,),
            ).fetchone()
            if existing is None:
                merged_content = content.strip()
                title = name.strip()
                metadata = _memory_metadata("memory", scope=scope, namespace=namespace)
            else:
                merged_content = existing["content"].rstrip() + "\n\n" + content.strip()
                title = existing["title"]
                metadata = json.loads(existing["metadata_json"])

            self._upsert_document(
                connection=connection,
                doc_id=memory_id,
                doc_type="memory",
                title=title,
                content=merged_content,
                source="memory",
                metadata=metadata,
            )

            state = self._row_to_state(
                connection.execute("SELECT * FROM runtime_state WHERE singleton = 1").fetchone()
            )
            state["pending_writes"] = 0
            state["focus"]["past"] = f"Updated memory {memory_id}"
            self._save_state(connection, state)
            return memory_id

    def log_episode(
        self,
        summary: str,
        *,
        title: str | None = None,
        tags: list[str] | None = None,
        occurred_at: str | None = None,
        scope: str = "global",
        namespace: str | None = None,
    ) -> str:
        normalized_summary = summary.strip()
        if not normalized_summary:
            raise ValueError("Episode summary must not be empty.")
        timestamp = occurred_at.strip() if occurred_at else _utc_now()
        day = _date_only(timestamp)
        slug = _slugify((title or normalized_summary[:48]).strip())
        episode_id = f"episode/{day}/{slug or 'entry'}"
        with self._connect() as connection:
            suffix = 1
            candidate_id = episode_id
            while connection.execute("SELECT 1 FROM documents WHERE doc_id = ?", (candidate_id,)).fetchone():
                suffix += 1
                candidate_id = f"{episode_id}-{suffix}"
            episode_id = candidate_id

            self._upsert_document(
                connection=connection,
                doc_id=episode_id,
                doc_type="episode",
                title=(title or f"Episode {day}").strip(),
                content=normalized_summary,
                source="episode",
                metadata=_memory_metadata(
                    "episodic",
                    scope=scope,
                    namespace=namespace,
                    tags=sorted({_slugify(tag) for tag in (tags or []) if _slugify(tag)}),
                    occurred_at=timestamp,
                ),
            )

            state = self._row_to_state(
                connection.execute("SELECT * FROM runtime_state WHERE singleton = 1").fetchone()
            )
            state["pending_writes"] = 0
            state["focus"]["past"] = f"Logged episode {episode_id}"
            self._save_state(connection, state)
            return episode_id

    def resume_context(
        self,
        query: str | None = None,
        *,
        memory_limit: int = 5,
        episode_limit: int = 5,
        days: int = 7,
        scope: str | None = None,
        namespace: str | None = None,
        include_global: bool = True,
    ) -> dict[str, Any]:
        semantic = (
            self.search_documents(
                query,
                {"memory"},
                limit=memory_limit,
                scope=scope,
                namespace=namespace,
                include_global=include_global,
            )
            if query and query.strip()
            else self.latest_memories(
                memory_limit,
                scope=scope,
                namespace=namespace,
                include_global=include_global,
            )
        )
        episodic = (
            self.search_documents(
                query,
                {"episode"},
                limit=episode_limit,
                scope=scope,
                namespace=namespace,
                include_global=include_global,
            )
            if query and query.strip()
            else self.recent_episodes(
                limit=episode_limit,
                days=days,
                scope=scope,
                namespace=namespace,
                include_global=include_global,
            )
        )
        return {
            "query": query or "",
            "semantic": semantic,
            "episodic": episodic,
            "gate_reason": _memory_gate_reason(query),
            "scope": _normalize_memory_scope(scope) if scope else None,
            "namespace": _normalize_namespace(namespace),
        }

    def latest_project_status(
        self,
        query: str | None = None,
        *,
        semantic_limit: int = 5,
        episode_limit: int = 5,
        days: int = 14,
        scope: str | None = None,
        namespace: str | None = None,
        include_global: bool = True,
    ) -> dict[str, Any]:
        semantic = self.resume_context(
            query=query,
            memory_limit=max(semantic_limit, 8),
            episode_limit=episode_limit,
            days=days,
            scope=scope,
            namespace=namespace,
            include_global=include_global,
        )["semantic"]
        project_facts = [
            item
            for item in semantic
            if item.get("metadata", {}).get("category") in {"project_detail", "workflow", "technical"}
        ][: max(1, semantic_limit)]
        if not project_facts:
            project_facts = semantic[: max(1, semantic_limit)]
        recent_episodes = self.recent_episodes(
            limit=episode_limit,
            days=days,
            scope=scope,
            namespace=namespace,
            include_global=include_global,
        )
        recommended_focus = None
        if recent_episodes:
            recommended_focus = recent_episodes[0]["title"]
        elif project_facts:
            recommended_focus = project_facts[0]["title"]
        return {
            "query": query or "",
            "project_facts": project_facts,
            "recent_episodes": recent_episodes,
            "recommended_focus": recommended_focus,
            "scope": _normalize_memory_scope(scope) if scope else None,
            "namespace": _normalize_namespace(namespace),
        }

    def reset_state(self) -> dict[str, Any]:
        with self._connect() as connection:
            state = self.default_state()
            self._save_state(connection, state)
        return self.state_response()

    def set_mode(self, mode: str) -> dict[str, Any]:
        normalized = _normalize_mode_name(mode)
        with self._connect() as connection:
            state = self._row_to_state(connection.execute("SELECT * FROM runtime_state WHERE singleton = 1").fetchone())
            sliders = dict(SLIDER_DEFAULTS)
            sliders.update(MODE_PRESETS[normalized])
            state["mode"] = normalized
            state["sliders"] = sliders
            state["system"]["vibe"] = {
                "base": "Focused",
                "research": "Analytical",
                "creative": "Creative",
                "technical": "Direct",
                "concise": "Focused",
            }[normalized]
            self._save_state(connection, state)
        return self.state_response()

    def set_slider(self, slider: str, value: int) -> dict[str, Any]:
        normalized = _normalize_slider_name(slider)
        if value < 0 or value > 100:
            raise ValueError("Slider value must be between 0 and 100.")
        with self._connect() as connection:
            state = self._row_to_state(connection.execute("SELECT * FROM runtime_state WHERE singleton = 1").fetchone())
            state["sliders"][normalized] = value
            self._save_state(connection, state)
        return self.state_response()

    def update_focus(
        self,
        present: str,
        past: str | None = None,
        future: str | None = None,
    ) -> dict[str, Any]:
        with self._connect() as connection:
            state = self._row_to_state(connection.execute("SELECT * FROM runtime_state WHERE singleton = 1").fetchone())
            state["focus"]["present"] = present.strip()
            if past is not None:
                state["focus"]["past"] = past.strip()
            if future is not None:
                state["focus"]["future"] = future.strip()
            self._save_state(connection, state)
        return self.state_response()

    def set_system_state(
        self,
        context: str | None = None,
        tools: str | None = None,
        vibe: str | None = None,
    ) -> dict[str, Any]:
        with self._connect() as connection:
            state = self._row_to_state(connection.execute("SELECT * FROM runtime_state WHERE singleton = 1").fetchone())
            if context is not None:
                state["system"]["context"] = context.strip()
            if tools is not None:
                state["system"]["tools"] = tools.strip()
            if vibe is not None:
                state["system"]["vibe"] = vibe.strip()
            self._save_state(connection, state)
        return self.state_response()

    def build_system_prompt(
        self,
        task: str | None = None,
        include_memory_summary: bool = True,
        memory_limit: int = 8,
    ) -> str:
        state = self.state_response()
        gate_reason = _memory_gate_reason(task)
        core_ids = [
            "system/core",
            "protocol/retrieval",
            "protocol/verification",
            "profile/defaults",
            MODE_TO_PERSONALITY_ID[state["mode"]],
        ]
        if include_memory_summary:
            core_ids.append("protocol/memory")
            core_ids.append("protocol/episodes")

        core_documents = [self.read_document(doc_id) for doc_id in core_ids]

        related_documents: list[dict[str, Any]] = []
        if task:
            for match in self.search_documents(task, {"system", "protocol", "profile", "mode"}, limit=4):
                if match["id"] not in core_ids:
                    related_documents.append(self.read_document(match["id"]))

        memory_lines: list[str] = []
        episode_lines: list[str] = []
        if include_memory_summary and task and gate_reason:
            memory_matches = self.search_documents(task, {"memory"}, limit=memory_limit)
            memory_lines = [f"- {match['id']}: {match['excerpt']}" for match in memory_matches]
            episode_matches = self.search_documents(task, {"episode"}, limit=max(3, min(memory_limit, 6)))
            episode_lines = [f"- {match['id']}: {match['excerpt']}" for match in episode_matches]

        sections = [
            "# AtlasNode runtime prompt",
            "",
            "## Runtime state",
            f"- mode: {state['mode']}",
            f"- personality: {state['personality_file']}",
            f"- session: {state['session']}",
            f"- focus.past: {state['focus']['past']}",
            f"- focus.present: {state['focus']['present']}",
            f"- focus.future: {state['focus']['future']}",
            f"- system.context: {state['system']['context']}",
            f"- system.tools: {state['system']['tools']}",
            f"- system.vibe: {state['system']['vibe']}",
            f"- storage.backend: {state['storage_backend']}",
            f"- embeddings: {state['embedding_backend']}",
            f"- storage.path: {state['memory']['store_path']}",
            "- sliders:",
        ]
        sections.extend(
            f"  - {name}: {value}%"
            for name, value in sorted(state["sliders"].items())
        )

        if task:
            sections.extend(["", "## Current task", task.strip()])

        sections.extend(
            [
                "",
                "## Retrieval gate",
                "- First ask whether the current request can be answered from the active chat and runtime state alone.",
                "- Consult semantic memory only when the task depends on prior preferences, durable facts, project details, or missing context.",
                "- Consult episodic history only when the task depends on recent events, prior sessions, progress, or what happened over time.",
                f"- memory_gate: {'open - ' + gate_reason if gate_reason else 'closed - no strong signal that stored context is required yet'}",
            ]
        )

        sections.extend(["", "## Core directives"])
        for document in core_documents:
            sections.extend(
                [
                    f"### {document['id']}",
                    document["content"],
                    "",
                ]
            )

        if related_documents:
            sections.append("## Retrieved supporting context")
            for document in related_documents:
                sections.extend(
                    [
                        f"### {document['id']}",
                        document["content"],
                        "",
                    ]
                )

        if memory_lines:
            sections.extend(["## Relevant memory", *memory_lines, ""])
        if episode_lines:
            sections.extend(["## Relevant episodic history", *episode_lines, ""])

        prompt = "\n".join(sections).strip() + "\n"
        with self._connect() as connection:
            self._record_usage_event(
                connection,
                "prompt",
                _byte_length(prompt),
                metadata={
                    "task": task or "",
                    "include_memory_summary": include_memory_summary,
                    "memory_limit": memory_limit,
                },
            )
        return prompt

    def _daily_usage_rows(
        self,
        connection: sqlite3.Connection,
        *,
        event_types: set[str],
        days: int,
    ) -> dict[str, int]:
        if not event_types:
            return {}
        placeholders = ", ".join("?" for _ in event_types)
        rows = connection.execute(
            f"""
            SELECT substr(occurred_at, 1, 10) AS day, SUM(byte_count) AS total_bytes
            FROM usage_events
            WHERE event_type IN ({placeholders})
            GROUP BY day
            ORDER BY day DESC
            LIMIT ?
            """,
            (*sorted(event_types), days),
        ).fetchall()
        return {row["day"]: int(row["total_bytes"] or 0) for row in rows}

    def _daily_document_creation_rows(self, connection: sqlite3.Connection, days: int) -> dict[str, int]:
        rows = connection.execute(
            """
            SELECT substr(created_at, 1, 10) AS day, SUM(length(title) + length(content)) AS total_bytes
            FROM documents
            GROUP BY day
            ORDER BY day DESC
            LIMIT ?
            """,
            (days,),
        ).fetchall()
        return {row["day"]: int(row["total_bytes"] or 0) for row in rows}

    def _memory_keyword_candidates(self, memory_rows: list[sqlite3.Row]) -> list[str]:
        counts: dict[str, int] = {}
        for row in memory_rows:
            tokens = {
                token
                for token in _tokenize(f"{row['title']} {row['doc_id']}")
                if len(token) >= 4 and token not in ANALYTICS_STOPWORDS
            }
            for token in tokens:
                counts[token] = counts.get(token, 0) + 1
        return [
            token
            for token, _ in sorted(counts.items(), key=lambda item: (-item[1], item[0]))[:10]
        ]

    def _memory_category_name(self, row: sqlite3.Row, candidates: list[str]) -> str:
        tokens = _tokenize(f"{row['title']} {row['doc_id']} {row['content'][:240]}")
        for candidate in candidates:
            if candidate in tokens:
                return candidate
        return "general"

    def _category_summary(self, connection: sqlite3.Connection) -> list[dict[str, Any]]:
        rows = connection.execute(
            """
            SELECT doc_id, doc_type, title, content
            FROM documents
            ORDER BY doc_type, doc_id
            """
        ).fetchall()
        memory_rows = [row for row in rows if row["doc_type"] == "memory"]
        memory_candidates = self._memory_keyword_candidates(memory_rows)

        category_map: dict[str, dict[str, Any]] = {}
        for row in rows:
            base_key = row["doc_type"]
            entry = category_map.setdefault(
                base_key,
                {
                    "id": base_key,
                    "label": base_key.title(),
                    "count": 0,
                    "bytes": 0,
                    "children": {},
                },
            )
            doc_bytes = _byte_length(row["title"]) + _byte_length(row["content"])
            entry["count"] += 1
            entry["bytes"] += doc_bytes

            if row["doc_type"] == "memory":
                child_label = self._memory_category_name(row, memory_candidates)
            else:
                parts = row["doc_id"].split("/", 1)
                child_label = parts[1] if len(parts) > 1 else row["doc_id"]

            child = entry["children"].setdefault(
                child_label,
                {"id": f"{base_key}:{child_label}", "label": child_label.replace("_", " ").title(), "count": 0, "bytes": 0},
            )
            child["count"] += 1
            child["bytes"] += doc_bytes

        categories = []
        for value in sorted(category_map.values(), key=lambda item: (-item["bytes"], item["label"])):
            children = sorted(value["children"].values(), key=lambda item: (-item["bytes"], item["label"]))[:8]
            categories.append(
                {
                    "id": value["id"],
                    "label": value["label"],
                    "count": value["count"],
                    "bytes": value["bytes"],
                    "children": children,
                }
            )
        return categories

    def dashboard_snapshot(self, days: int = DEFAULT_ANALYTICS_DAYS) -> dict[str, Any]:
        with self._connect() as connection:
            state = self.state_response()
            document_rows = connection.execute(
                """
                SELECT doc_type, COUNT(*) AS doc_count, SUM(length(title) + length(content)) AS content_bytes
                FROM documents
                GROUP BY doc_type
                ORDER BY doc_type
                """
            ).fetchall()
            stored_breakdown = [
                {
                    "type": row["doc_type"],
                    "count": int(row["doc_count"] or 0),
                    "bytes": int(row["content_bytes"] or 0),
                }
                for row in document_rows
            ]
            total_stored_bytes = sum(item["bytes"] for item in stored_breakdown)
            db_file_bytes = self.db_path.stat().st_size if self.db_path.exists() else 0

            daily_added = self._daily_usage_rows(connection, event_types={"write"}, days=days)
            if not daily_added:
                daily_added = self._daily_document_creation_rows(connection, days)

            daily_retrieved = self._daily_usage_rows(connection, event_types={"read", "search", "prompt"}, days=days)
            has_retrieval_history = connection.execute(
                "SELECT 1 FROM usage_events WHERE event_type IN ('read', 'search', 'prompt') LIMIT 1"
            ).fetchone() is not None

            return {
                "state": state,
                "storage": {
                    "db_file_bytes": db_file_bytes,
                    "stored_content_bytes": total_stored_bytes,
                    "document_count": sum(item["count"] for item in stored_breakdown),
                    "memory_count": self._memory_count(connection),
                    "episode_count": int(
                        connection.execute(
                            "SELECT COUNT(*) AS count FROM documents WHERE doc_type = 'episode'"
                        ).fetchone()["count"]
                    ),
                    "breakdown": stored_breakdown,
                },
                "usage": {
                    "days": days,
                    "daily_added_bytes": daily_added,
                    "daily_retrieved_bytes": daily_retrieved,
                    "retrieval_history_available": has_retrieval_history,
                    "retrieval_history_note": (
                        "Retrieval history is only available from the point this AtlasNode build began logging usage events."
                    ),
                },
                "categories": self._category_summary(connection),
                "recent_context": {
                    "semantic": self.latest_memories(4),
                    "episodic": self.recent_episodes(limit=4, days=max(days, 7)),
                },
            }

