import unittest

from app.rag.cache import MemoryRagCache, RedisRagCache, make_cache_key
from demo.providers import build_demo_knowledge_service


class CountingKnowledgeAdapter:
    def __init__(self, delegate):
        self.delegate = delegate
        self.intent_calls = 0
        self.answer_calls = 0

    def classify_knowledge(self, *, prompt, question):
        self.intent_calls += 1
        return self.delegate.classify_knowledge(prompt=prompt, question=question)

    def answer_knowledge(self, *, prompt, question, citations):
        self.answer_calls += 1
        return self.delegate.answer_knowledge(prompt=prompt, question=question, citations=citations)


class FakeRedis:
    def __init__(self):
        self.values = {}

    def get(self, key):
        return self.values.get(key)

    def set(self, key, value, ex):
        self.values[key] = value
        self.last_ttl = ex
        return True

    def delete(self, key):
        self.values.pop(key, None)

    def ping(self):
        return True


class RagCacheTests(unittest.TestCase):
    def test_memory_cache_proves_miss_write_verified_then_hit_without_model_calls(self):
        service = build_demo_knowledge_service()
        adapter = CountingKnowledgeAdapter(service.intent_adapter)
        service.intent_adapter = adapter
        service.answer_adapter = adapter
        service.cache = MemoryRagCache(verify_write=True)

        question = "北京公积金贷款婚姻电子证照可以免交纸质件吗？"
        first = service.query(question)
        calls_after_first = (adapter.intent_calls, adapter.answer_calls)
        second = service.query(question)

        self.assertEqual(first["trace"]["cache"]["status"], "MISS")
        self.assertTrue(first["trace"]["cache"]["write_verified"])
        self.assertEqual(second["trace"]["cache"]["status"], "HIT")
        self.assertEqual((adapter.intent_calls, adapter.answer_calls), calls_after_first)
        self.assertEqual(first["citations"], second["citations"])

    def test_cache_key_isolated_by_question_and_index_version(self):
        first = make_cache_key("rag", {"question": "北京材料", "index_version": "v1"})
        other_region = make_cache_key("rag", {"question": "南京材料", "index_version": "v1"})
        other_version = make_cache_key("rag", {"question": "北京材料", "index_version": "v2"})
        self.assertEqual(len({first, other_region, other_version}), 3)

    def test_knowledge_cache_isolated_by_model_signature(self):
        service = build_demo_knowledge_service()
        service.cache = MemoryRagCache(verify_write=True)
        question = "北京公积金贷款婚姻电子证照可以免交纸质件吗？"

        first = service.query(question)
        service.cache_model_signature = "another-model-route"
        second = service.query(question)

        self.assertEqual(first["trace"]["cache"]["status"], "MISS")
        self.assertEqual(second["trace"]["cache"]["status"], "MISS")

    def test_redis_adapter_returns_read_after_write_receipt(self):
        client = FakeRedis()
        cache = RedisRagCache("redis://unused", client=client, verify_write=True)
        receipt = cache.set("rag:key", {"status": "ANSWERED"}, ttl_seconds=60)
        self.assertTrue(receipt.stored)
        self.assertTrue(receipt.verified)
        self.assertEqual(cache.get("rag:key").value, {"status": "ANSWERED"})
        self.assertEqual(client.last_ttl, 60)


if __name__ == "__main__":
    unittest.main()
