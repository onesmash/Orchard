"""Tests for the Ollama Embedder client."""

from __future__ import annotations

from unittest import mock

import httpx
import pytest

from orchard.search.embedder import Embedder, EmbeddingError


def _mock_embed_response_1024d() -> mock.Mock:
    """Return a mock httpx.Response that simulates a 1024-dim embedding."""
    resp = mock.Mock(spec=httpx.Response)
    resp.status_code = 200
    resp.json.return_value = {
        "embeddings": [[float(i) for i in range(1024)]],
    }
    resp.raise_for_status = mock.Mock()
    return resp


class TestEmbedder:
    """Test suite for Embedder."""

    def test_embedder_returns_1024d_vector(self) -> None:
        """embed() returns a list of exactly 1024 floats."""
        embedder = Embedder()
        with mock.patch.object(embedder._client, "post") as mock_post:
            mock_post.return_value = _mock_embed_response_1024d()

            vec = embedder.embed("hello world")

        assert len(vec) == 1024
        assert isinstance(vec, list)
        assert all(isinstance(v, float) for v in vec)

    def test_embedder_unreachable_raises(self) -> None:
        """ConnectError is wrapped in EmbeddingError."""
        embedder = Embedder()
        with mock.patch.object(embedder._client, "post") as mock_post:
            mock_post.side_effect = httpx.ConnectError(
                "Connection refused"
            )

            with pytest.raises(EmbeddingError) as excinfo:
                embedder.embed("hello world")

        assert "Ollama unreachable" in str(excinfo.value)

    def test_embed_batch_returns_list_of_1024d_vectors(self) -> None:
        """embed_batch() returns a list of 1024-dim vectors, one per input."""
        embedder = Embedder()
        texts = ["hello", "world", "foo"]
        with mock.patch.object(embedder._client, "post") as mock_post:
            mock_resp = mock.Mock(spec=httpx.Response)
            mock_resp.status_code = 200
            mock_resp.json.return_value = {
                "embeddings": [
                    [float(j) for j in range(1024)] for _ in texts
                ]
            }
            mock_resp.raise_for_status = mock.Mock()
            mock_post.return_value = mock_resp

            result = embedder.embed_batch(texts)

        assert len(result) == len(texts)
        for vec in result:
            assert len(vec) == 1024

    def test_embed_batch_unreachable_raises(self) -> None:
        """embed_batch() wraps exceptions in EmbeddingError."""
        embedder = Embedder()
        with mock.patch.object(embedder._client, "post") as mock_post:
            mock_post.side_effect = httpx.ConnectError(
                "Connection refused"
            )

            with pytest.raises(EmbeddingError) as excinfo:
                embedder.embed_batch(["hello", "world"])

        assert "batch failed" in str(excinfo.value)

    def test_embed_batch_http_error_raises_embedding_error(self) -> None:
        """Non-2xx status code triggers EmbeddingError in embed_batch."""
        embedder = Embedder()
        with mock.patch.object(embedder._client, "post") as mock_post:
            mock_resp = mock.Mock(spec=httpx.Response)
            mock_resp.raise_for_status.side_effect = httpx.HTTPStatusError(
                "400 Bad Request", request=mock.Mock(), response=mock_resp
            )
            mock_post.return_value = mock_resp

            with pytest.raises(EmbeddingError):
                embedder.embed_batch(["hello"])
