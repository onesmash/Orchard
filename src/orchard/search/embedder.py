from __future__ import annotations

import httpx


class EmbeddingError(Exception):
    """Raised when embedding requests fail."""


class Embedder:
    """Thin client for Ollama /api/embed endpoint.

    Produces 768-dimensional embedding vectors using a local Ollama model
    (default: qwen3-embedding:0.6b).
    """

    def __init__(
        self,
        base_url: str = "http://localhost:11434",
        model: str = "qwen3-embedding:0.6b",
        timeout: float = 30,
    ) -> None:
        self._url = f"{base_url.rstrip('/')}/api/embed"
        self._model = model
        self._client = httpx.Client(timeout=timeout)

    def embed(self, text: str) -> list[float]:
        """Embed a single text string into a 768-dimensional vector."""
        try:
            r = self._client.post(
                self._url, json={"model": self._model, "input": [text]}
            )
            r.raise_for_status()
            return r.json()["embeddings"][0]
        except httpx.ConnectError as e:
            raise EmbeddingError(f"Ollama unreachable: {e}") from e

    def embed_batch(self, texts: list[str]) -> list[list[float]]:
        """Embed a batch of text strings into 768-dimensional vectors."""
        try:
            r = self._client.post(
                self._url, json={"model": self._model, "input": texts}
            )
            r.raise_for_status()
            return r.json()["embeddings"]
        except Exception as e:
            raise EmbeddingError(f"batch failed: {e}") from e
