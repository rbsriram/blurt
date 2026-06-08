"""Embeddings via a local Ollama server.

We talk to Ollama's batch /api/embed endpoint over a shared async client, so the
event loop never blocks on a network call. nomic-embed-text was trained with
task prefixes ("search_document:" / "search_query:"); applying them is the
single biggest quality lever for retrieval, so it is on by default.

The Embedder is deliberately a thin, swappable seam: anything offering
embed_documents / embed_query / health can replace it (e.g. an in-process
fastembed backend) without the rest of the app noticing.
"""

from __future__ import annotations

import httpx


class EmbedderError(RuntimeError):
    pass


class OllamaEmbedder:
    def __init__(
        self,
        *,
        url: str,
        model: str,
        dim: int,
        use_prefixes: bool = True,
        timeout_s: float = 30.0,
        keep_alive: str = "30m",
        pull_timeout_s: float = 900.0,
    ):
        self.url = url.rstrip("/")
        self.model = model
        self.dim = dim
        self.use_prefixes = use_prefixes
        self.keep_alive = keep_alive
        self.pull_timeout_s = pull_timeout_s
        self._client = httpx.AsyncClient(base_url=self.url, timeout=timeout_s)

    async def aclose(self) -> None:
        await self._client.aclose()

    # ---- internals -----------------------------------------------------

    def _doc(self, text: str) -> str:
        return f"search_document: {text}" if self.use_prefixes else text

    def _query(self, text: str) -> str:
        return f"search_query: {text}" if self.use_prefixes else text

    async def _embed_raw(self, inputs: list[str]) -> list[list[float]]:
        if not inputs:
            return []
        try:
            r = await self._client.post(
                "/api/embed",
                json={"model": self.model, "input": inputs, "keep_alive": self.keep_alive},
            )
            r.raise_for_status()
        except httpx.HTTPError as e:
            raise EmbedderError(f"Ollama embed failed: {e}") from e
        embeddings = r.json().get("embeddings")
        if not embeddings or len(embeddings) != len(inputs):
            raise EmbedderError("Ollama returned an unexpected embedding payload")
        return embeddings

    # ---- public API ----------------------------------------------------

    async def embed_documents(self, texts: list[str]) -> list[list[float]]:
        return await self._embed_raw([self._doc(t) for t in texts])

    async def embed_document_one(self, text: str) -> list[float]:
        return (await self.embed_documents([text]))[0]

    async def embed_query(self, text: str) -> list[float]:
        return (await self._embed_raw([self._query(text)]))[0]

    async def ensure_model(self) -> bool:
        """Make sure the embedding model is present, pulling it if not. Returns whether the
        model is available afterward. Used to self-heal when Ollama is installed/started after
        Blurt is already running (the launcher only pulls at boot). Safe to call repeatedly:
        pulling an already-present model is a no-op, and an unreachable server just returns
        False. The pull moves ~270MB once, hence its own long timeout."""
        reachable, available = await self.health()
        if not reachable:
            return False
        if available:
            return True
        try:
            r = await self._client.post(
                "/api/pull",
                json={"model": self.model, "stream": False},
                timeout=self.pull_timeout_s,
            )
            r.raise_for_status()
        except httpx.HTTPError:
            return False
        _, available = await self.health()
        return available

    async def health(self) -> tuple[bool, bool]:
        """Return (server_reachable, embed_model_available)."""
        try:
            v = await self._client.get("/api/version")
            v.raise_for_status()
        except httpx.HTTPError:
            return (False, False)
        try:
            tags = await self._client.get("/api/tags")
            tags.raise_for_status()
            names = {m.get("name", "") for m in tags.json().get("models", [])}
        except httpx.HTTPError:
            return (True, False)
        base = self.model.split(":")[0]
        available = any(n == self.model or n.split(":")[0] == base for n in names)
        return (True, available)
