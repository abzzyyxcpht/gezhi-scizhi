# -*- coding: utf-8 -*-
"""Embedding 向量级单元测试：cosine 边界、磁盘缓存、HTTP 降级、惰性单例。"""
from __future__ import annotations

import os
import unittest
from unittest import mock

from src import embedding


class CountingProvider(embedding.HttpEmbeddingProvider):
    """不触网的 HTTP 提供方桩：只数请求次数，返回可预期的向量。"""

    def __init__(self, model: str = "counting") -> None:
        super().__init__("http://localhost:9/v1", "", model)
        self.calls = 0

    def _request(self, texts: list[str]) -> list[list[float]] | None:
        self.calls += 1
        return [[1.0, 0.0] for _ in texts]


class CosineTest(unittest.TestCase):
    def test_identical_vectors(self):
        self.assertAlmostEqual(embedding.cosine([1.0, 2.0], [[1.0, 2.0]])[0], 1.0, places=6)

    def test_orthogonal_vectors(self):
        self.assertAlmostEqual(embedding.cosine([1.0, 0.0], [[0.0, 1.0]])[0], 0.0, places=6)

    def test_zero_vector_is_safe(self):
        self.assertEqual(embedding.cosine([0.0, 0.0], [[1.0, 1.0], [0.0, 0.0]]), [0.0, 0.0])

    def test_empty_docs(self):
        self.assertEqual(embedding.cosine([1.0], []), [])


class HttpEmbeddingProviderTest(unittest.TestCase):
    def setUp(self):
        embedding.clear_cache()
        self.addCleanup(embedding.clear_cache)

    def test_endpoint_compatibility(self):
        cases = {
            "http://host": "http://host/v1/embeddings",
            "http://host/": "http://host/v1/embeddings",
            "http://host/v1": "http://host/v1/embeddings",
            "http://host/api/v1/": "http://host/api/v1/embeddings",
        }
        for base, expected in cases.items():
            with self.subTest(base=base):
                provider = embedding.HttpEmbeddingProvider(base, "", "m")
                self.assertEqual(provider._endpoint(), expected)

    def test_dead_port_returns_none(self):
        provider = embedding.HttpEmbeddingProvider("http://127.0.0.1:1/v1", "", "m")
        self.assertIsNone(provider.embed(["dead-port-probe-7f3a"]))
        self.assertIsNone(provider.embed(["dead-port-probe-7f3a", "dead-port-probe-7f3b"]))

    def test_empty_input_returns_empty(self):
        provider = CountingProvider()
        self.assertEqual(provider.embed([]), [])

    def test_cache_hit_avoids_second_request(self):
        provider = CountingProvider()
        first = provider.embed(["cache-probe-alpha", "cache-probe-beta"])
        self.assertEqual(provider.calls, 1)
        self.assertEqual(first, [[1.0, 0.0], [1.0, 0.0]])
        second = provider.embed(["cache-probe-beta", "cache-probe-alpha"])  # 顺序打乱仍全命中
        self.assertEqual(provider.calls, 1)
        self.assertEqual(second, first)

    def test_partial_cache_hit_only_requests_missing(self):
        provider = CountingProvider()
        provider.embed(["cache-probe-one"])
        self.assertEqual(provider.calls, 1)
        out = provider.embed(["cache-probe-one", "cache-probe-two"])
        self.assertEqual(provider.calls, 2)  # 只补请求缺失的一篇
        self.assertEqual(len(out), 2)


class GetProviderTest(unittest.TestCase):
    def tearDown(self):
        embedding.reset_provider()

    def test_missing_config_returns_none(self):
        self.assertIsNone(embedding.get_provider({}))
        self.assertIsNone(embedding.get_provider({"embedding_base_url": "http://x/v1"}))
        self.assertIsNone(embedding.get_provider({"embedding_model": "m"}))

    def test_singleton_rebuilds_on_signature_change(self):
        p1 = embedding.get_provider({"embedding_base_url": "http://a/v1", "embedding_model": "m"})
        p1_again = embedding.get_provider({"embedding_base_url": "http://a/v1", "embedding_model": "m"})
        self.assertIs(p1, p1_again)
        p2 = embedding.get_provider({"embedding_base_url": "http://b/v1", "embedding_model": "m"})
        self.assertIsNot(p1, p2)
        self.assertIs(p2, embedding.get_provider({"embedding_base_url": "http://b/v1", "embedding_model": "m"}))
        embedding.reset_provider()
        p3 = embedding.get_provider({"embedding_base_url": "http://b/v1", "embedding_model": "m"})
        self.assertIsNot(p3, p2)

    def test_env_vars_take_precedence(self):
        env = {"GEZHI_EMB_BASE_URL": "http://env/v1", "GEZHI_EMB_MODEL": "env-model",
               "GEZHI_EMB_API_KEY": "env-key"}
        with mock.patch.dict(os.environ, env, clear=False):
            embedding.reset_provider()
            provider = embedding.get_provider({"embedding_base_url": "http://cfg/v1",
                                               "embedding_model": "cfg-model"})
            self.assertIsInstance(provider, embedding.HttpEmbeddingProvider)
            self.assertEqual(provider.base_url, "http://env/v1")
            self.assertEqual(provider.model, "env-model")
            self.assertEqual(provider.api_key, "env-key")
        # env 与 settings 互补合成完整配置（model 来自 env，base_url 来自 settings）
        with mock.patch.dict(os.environ, {"GEZHI_EMB_MODEL": "only-model"}, clear=False):
            os.environ.pop("GEZHI_EMB_BASE_URL", None)
            os.environ.pop("GEZHI_EMB_API_KEY", None)
            embedding.reset_provider()
            provider = embedding.get_provider({"embedding_base_url": "http://cfg/v1"})
            self.assertIsNotNone(provider)
            self.assertEqual(provider.model, "only-model")
            # 双方加起来仍缺一：env 只有 model、settings 无 base_url → 不可用
            self.assertIsNone(embedding.get_provider({}))


if __name__ == "__main__":
    unittest.main()
