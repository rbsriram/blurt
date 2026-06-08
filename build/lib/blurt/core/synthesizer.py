"""Optional LLM synthesis over retrieved entries.

Strictly opt-in (CHAT_ENABLED). When off, the API returns 503 and nothing here
runs. When on, it asks a local Ollama chat model to answer from the retrieved
entries, instructed to prefer the most recent information on conflicts. Never
calls any external service.
"""

from __future__ import annotations

import httpx

_SYSTEM = (
    "You answer strictly from the user's own notes provided below. "
    "If notes conflict, prefer the most recent. If the notes do not contain the "
    "answer, say so plainly. Be concise."
)


class Synthesizer:
    def __init__(self, *, url: str, model: str, timeout_s: float = 60.0):
        self._client = httpx.AsyncClient(base_url=url.rstrip("/"), timeout=timeout_s)
        self._model = model

    async def aclose(self) -> None:
        await self._client.aclose()

    async def synthesize(self, query: str, entries: list[dict]) -> str:
        context = "\n\n".join(
            f"[{e.get('created_at', '')}] {e.get('content', '')}" for e in entries
        )
        prompt = f"Notes:\n{context}\n\nQuestion: {query}"
        r = await self._client.post(
            "/api/chat",
            json={
                "model": self._model,
                "stream": False,
                "messages": [
                    {"role": "system", "content": _SYSTEM},
                    {"role": "user", "content": prompt},
                ],
            },
        )
        r.raise_for_status()
        return r.json()["message"]["content"].strip()
