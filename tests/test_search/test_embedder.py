"""Tests for the llama.cpp Embedder client."""

from __future__ import annotations

from unittest import mock

import pytest

from orchard.search.embedder import Embedder, EmbeddingError


def _make_vec1024(base: float = 0.0, step: float = 0.0) -> list[float]:
    """Return a 1024-dimensional float vector for testing."""
    return [base + i * step for i in range(1024)]


class TestEmbedder:
    """Test suite for Embedder (llama.cpp backend)."""

    @mock.patch("orchard.search.embedder.Llama")
    @mock.patch("pathlib.Path.exists", return_value=True)
    def test_embed_returns_1024d_vector(self, mock_exists, mock_llama_cls) -> None:
        """embed() returns a list of exactly 1024 floats."""
        mock_llama = mock_llama_cls.return_value
        mock_llama.embed.return_value = _make_vec1024(step=0.001)

        embedder = Embedder()
        vec = embedder.embed("hello world")

        assert len(vec) == 1024
        assert isinstance(vec, list)
        assert all(isinstance(v, float) for v in vec)

    @mock.patch("orchard.search.embedder.Llama")
    @mock.patch("pathlib.Path.exists", return_value=True)
    def test_embed_wraps_errors(self, mock_exists, mock_llama_cls) -> None:
        """llama.cpp errors are wrapped in EmbeddingError."""
        mock_llama = mock_llama_cls.return_value
        mock_llama.embed.side_effect = RuntimeError("GGML error")

        embedder = Embedder()
        with pytest.raises(EmbeddingError) as excinfo:
            embedder.embed("hello world")

        assert "embed failed" in str(excinfo.value)

    @mock.patch("orchard.search.embedder.Llama")
    @mock.patch("pathlib.Path.exists", return_value=True)
    def test_embed_batch_returns_list_of_1024d_vectors(
        self, mock_exists, mock_llama_cls
    ) -> None:
        """embed_batch() returns a list of 1024-dim vectors, one per input."""
        mock_llama = mock_llama_cls.return_value
        texts = ["hello", "world", "foo"]
        mock_llama.embed.return_value = [_make_vec1024(step=0.001) for _ in texts]

        embedder = Embedder()
        result = embedder.embed_batch(texts)

        assert len(result) == len(texts)
        for vec in result:
            assert len(vec) == 1024

    @mock.patch("orchard.search.embedder.Llama")
    @mock.patch("pathlib.Path.exists", return_value=True)
    def test_embed_batch_wraps_errors(self, mock_exists, mock_llama_cls) -> None:
        """embed_batch() wraps llama.cpp errors in EmbeddingError."""
        mock_llama = mock_llama_cls.return_value
        mock_llama.embed.side_effect = RuntimeError("GGML batch error")

        embedder = Embedder()
        with pytest.raises(EmbeddingError) as excinfo:
            embedder.embed_batch(["hello", "world"])

        assert "batch embed failed" in str(excinfo.value)

    @mock.patch("pathlib.Path.exists", return_value=False)
    def test_missing_model_raises_embedding_error(self, mock_exists) -> None:
        """When the model file is missing, EmbeddingError is raised."""
        with pytest.raises(EmbeddingError) as excinfo:
            Embedder(model_path="/nonexistent/model.gguf")

        assert "Model not found" in str(excinfo.value)

    @mock.patch("orchard.search.embedder.Llama")
    @mock.patch("pathlib.Path.exists", return_value=True)
    def test_embed_handles_nested_list_result(
        self, mock_exists, mock_llama_cls
    ) -> None:
        """Older llama-cpp-python versions may wrap embed() result in extra list."""
        mock_llama = mock_llama_cls.return_value
        mock_llama.embed.return_value = [_make_vec1024(step=0.001)]

        embedder = Embedder()
        vec = embedder.embed("test")

        assert len(vec) == 1024
        assert isinstance(vec[0], float)
