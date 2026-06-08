"""Runtime configuration.

Every value has a sane default and an environment-variable override, so the app
runs with zero config out of the box but is fully tunable for tests and power
users. Prefix for all overrides: BLURT_ (e.g. BLURT_PORT=8080).
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path

_ROOT = Path(__file__).resolve().parent


def _env(key: str, default: str) -> str:
    return os.environ.get(f"BLURT_{key}", default)


def _env_int(key: str, default: int) -> int:
    return int(_env(key, str(default)))


def _env_float(key: str, default: float) -> float:
    return float(_env(key, str(default)))


def _env_bool(key: str, default: bool) -> bool:
    return _env(key, "1" if default else "0").lower() in {"1", "true", "yes", "on"}


@dataclass(frozen=True)
class Settings:
    # --- Server ---
    host: str = field(default_factory=lambda: _env("HOST", "127.0.0.1"))  # localhost only, non-negotiable
    port: int = field(default_factory=lambda: _env_int("PORT", 7337))

    # --- Storage ---
    db_path: str = field(default_factory=lambda: _env("DB_PATH", str(_ROOT.parent / "blurt.db")))

    # --- Auto-export mirror ---
    # Keep a plain, human-readable scratchpad.md next to the DB always in sync, so
    # a Blurt-independent copy of everything exists on disk. On by default; the DB
    # stays the fast source of truth and the mirror is written off the request path.
    auto_export_md: bool = field(default_factory=lambda: _env_bool("AUTO_EXPORT_MD", True))
    auto_export_debounce_s: float = field(default_factory=lambda: _env_float("AUTO_EXPORT_DEBOUNCE_S", 1.0))

    # --- Embeddings (Ollama) ---
    ollama_url: str = field(default_factory=lambda: _env("OLLAMA_URL", "http://localhost:11434"))
    embed_model: str = field(default_factory=lambda: _env("EMBED_MODEL", "nomic-embed-text"))
    embed_dim: int = field(default_factory=lambda: _env_int("EMBED_DIM", 768))
    # nomic-embed-text is trained with task prefixes; using them materially
    # improves retrieval. Disable for models that do not expect them.
    embed_use_prefixes: bool = field(default_factory=lambda: _env_bool("EMBED_USE_PREFIXES", True))
    embed_timeout_s: float = field(default_factory=lambda: _env_float("EMBED_TIMEOUT_S", 30.0))
    # Keep the embedding model resident in Ollama so the ghost stays instant
    # instead of paying a cold model-load on the first keystroke after a pause.
    embed_keep_alive: str = field(default_factory=lambda: _env("EMBED_KEEP_ALIVE", "30m"))
    # Sub-batch size for embedding calls: balances interactive latency (a single
    # save embeds instantly) against bulk throughput. Local nomic on Apple
    # silicon runs ~10-25 docs/s, so large notes/imports are throughput-bound.
    embed_batch_size: int = field(default_factory=lambda: _env_int("EMBED_BATCH_SIZE", 48))
    # How many queued entries the worker may gather into one bulk pass.
    index_drain_cap: int = field(default_factory=lambda: _env_int("INDEX_DRAIN_CAP", 256))

    # --- Optional LLM synthesis (off by default; user opts in) ---
    chat_enabled: bool = field(default_factory=lambda: _env_bool("CHAT_ENABLED", False))
    chat_model: str = field(default_factory=lambda: _env("CHAT_MODEL", "llama3.2"))

    # --- Ghost suggestion ---
    # Tuned empirically against nomic-embed-text doc-doc scores: genuine semantic
    # matches (reworded, non-overlapping vocabulary) land ~0.64-0.91, while
    # unrelated notes sit below ~0.57. 0.62 fires on meaning, not just wording.
    ghost_similarity_threshold: float = field(default_factory=lambda: _env_float("GHOST_THRESHOLD", 0.62))
    # The peek is for mid-thought capture, not single-term lookup: a 2-word floor
    # keeps it from firing on noise (UX.md §2). Search handles single terms.
    ghost_min_words_server: int = field(default_factory=lambda: _env_int("GHOST_MIN_WORDS_SERVER", 2))
    ghost_debounce_ms: int = field(default_factory=lambda: _env_int("GHOST_DEBOUNCE_MS", 120))
    ghost_min_words_client: int = field(default_factory=lambda: _env_int("GHOST_MIN_WORDS_CLIENT", 2))
    ghost_max_chars_shown: int = field(default_factory=lambda: _env_int("GHOST_MAX_CHARS", 120))

    # --- Query / search ---
    query_top_chunks: int = field(default_factory=lambda: _env_int("QUERY_TOP_CHUNKS", 40))
    query_max_entries: int = field(default_factory=lambda: _env_int("QUERY_MAX_ENTRIES", 10))
    # Relevance floor used only when EXPORTING a filtered view, so unrelated
    # notes do not leak into a query-scoped export. /api/query stays unfiltered.
    export_query_min_score: float = field(default_factory=lambda: _env_float("EXPORT_QUERY_MIN_SCORE", 0.6))

    # --- Chunking ---
    chunk_size_words: int = field(default_factory=lambda: _env_int("CHUNK_SIZE_WORDS", 80))
    chunk_overlap_words: int = field(default_factory=lambda: _env_int("CHUNK_OVERLAP_WORDS", 20))
    chunk_single_max_words: int = field(default_factory=lambda: _env_int("CHUNK_SINGLE_MAX_WORDS", 100))

    # --- Limits / safety ---
    max_content_chars: int = field(default_factory=lambda: _env_int("MAX_CONTENT_CHARS", 1_000_000))

    # --- Test/dev only: enables destructive /api/test/* endpoints ---
    enable_test_endpoints: bool = field(default_factory=lambda: _env_bool("TESTING", False))

    @property
    def export_md_path(self) -> Path:
        # Sits beside the DB so the data and its mirror travel together.
        return Path(self.db_path).with_name("scratchpad.md")

    @property
    def static_dir(self) -> Path:
        return _ROOT / "static"

    @property
    def schema_path(self) -> Path:
        return _ROOT / "db" / "schema.sql"


settings = Settings()
