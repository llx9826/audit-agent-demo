import unittest

from app.rag.adapters import HashedDenseEncoder
from app.rag.requirements.corpus import load_requirement_corpus
from app.rag.requirements.milvus_index import MilvusIndexConfig, MilvusRequirementIndexer


class FakeMilvusClient:
    def __init__(self):
        self.upserts = []

    def has_collection(self, **_kwargs):
        return True

    def upsert(self, **kwargs):
        self.upserts.append(kwargs)


class FakeSchema:
    def __init__(self):
        self.fields = []
        self.functions = []

    def add_field(self, name, data_type, **kwargs):
        self.fields.append({"name": name, "data_type": data_type, **kwargs})

    def add_function(self, function):
        self.functions.append(function)


class FakeIndexParams:
    def __init__(self):
        self.indexes = []

    def add_index(self, **kwargs):
        self.indexes.append(kwargs)


class FakeSchemaMilvusClient(FakeMilvusClient):
    def __init__(self):
        super().__init__()
        self.schema = FakeSchema()
        self.index = FakeIndexParams()
        self.created = []

    def has_collection(self, **_kwargs):
        return False

    def create_schema(self, **_kwargs):
        return self.schema

    def prepare_index_params(self):
        return self.index

    def create_collection(self, **kwargs):
        self.created.append(kwargs)


class MilvusRequirementIndexerTests(unittest.TestCase):
    def test_collection_uses_explicit_chinese_analyzer_for_bm25(self):
        client = FakeSchemaMilvusClient()
        indexer = MilvusRequirementIndexer(
            client=client,
            encoder=HashedDenseEncoder(),
            config=MilvusIndexConfig(dense_dimension=64, bm25_tokenizer="jieba"),
        )
        indexer.ensure_collection()
        content = next(item for item in client.schema.fields if item["name"] == "content")
        self.assertTrue(content["enable_analyzer"])
        self.assertEqual(content["analyzer_params"], {"tokenizer": "jieba"})
        self.assertTrue(any(item["metric_type"] == "BM25" for item in client.index.indexes))

    def test_batch_upsert_contains_filterable_metadata_and_document_vectors(self):
        client = FakeMilvusClient()
        indexer = MilvusRequirementIndexer(
            client=client,
            encoder=HashedDenseEncoder(),
            config=MilvusIndexConfig(batch_size=2, dense_dimension=64),
        )
        records = load_requirement_corpus()[:3]
        self.assertEqual(indexer.upsert(records), 3)
        self.assertEqual([len(item["data"]) for item in client.upserts], [2, 1])
        row = client.upserts[0]["data"][0]
        self.assertTrue({
            "requirement_id", "content", "dense_vector", "product", "channel",
            "person_role", "status", "region", "branch", "effective_from", "effective_to",
        }.issubset(row))
        self.assertTrue(row["content"])
        self.assertTrue(row["dense_vector"])


if __name__ == "__main__":
    unittest.main()
