from __future__ import annotations

import os
from pathlib import Path

from llama_cpp import Llama


class EmbeddingError(Exception):
    """Raised when embedding requests fail."""


class Embedder:
    """Self-contained embedder backed by llama.cpp.

    Loads a GGUF embedding model directly in-process — no external server
    or HTTP call needed.  Produces 1024‑dimensional vectors.

    Default model path: ``$ORCHARD_EMBED_MODEL`` env var, falling back to
    ``~/.orchard/models/Qwen3-Embedding-0.6B-Q8_0.gguf``.
    """

    def __init__(
        self,
        model_path: str | None = None,
        n_threads: int | None = None,
        verbose: bool = False,
    ) -> None:
        if model_path is None:
            model_path = os.environ.get(
                "ORCHARD_EMBED_MODEL",
                str(Path.home() / ".orchard" / "models" / "Qwen3-Embedding-0.6B-Q8_0.gguf"),
            )
        if not Path(model_path).exists():
            raise EmbeddingError(
                f"Model not found: {model_path}. "
                "Download a GGUF embedding model or set ORCHARD_EMBED_MODEL "
                "to point to a valid .gguf file."
            )

        self._model_path = model_path
        self._model = Llama(
            model_path=model_path,
            embedding=True,
            verbose=verbose,
            n_threads=n_threads,
        )

    def embed(self, text: str) -> list[float]:
        """Embed a single text string into a 1024‑dimensional vector."""
        try:
            result = self._model.embed(text)
            # llama-cpp-python returns list[float] for a single string.
            if result and isinstance(result[0], list):
                # Older versions may wrap in an extra list.
                return result[0]
            return result
        except Exception as e:
            raise EmbeddingError(f"embed failed: {e}") from e

    def embed_batch(self, texts: list[str]) -> list[list[float]]:
        """Embed a batch of text strings into 1024‑dimensional vectors."""
        try:
            result = self._model.embed(texts)
            return result
        except Exception as e:
            raise EmbeddingError(f"batch embed failed: {e}") from e
