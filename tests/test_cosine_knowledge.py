import tempfile, unittest
import chromadb
from chromadb.config import Settings
from mcp.knowledge_base import KnowledgeBase

class CosineKnowledgeTests(unittest.TestCase):
    def test_migration_preserves_imports_vectors_and_legacy(self):
        with tempfile.TemporaryDirectory() as tmp:
            client=chromadb.PersistentClient(path=tmp,settings=Settings(anonymized_telemetry=False))
            old=client.get_or_create_collection('knowledge_base')
            old.add(ids=['imported','no-meta'],documents=['用户导入知识','other'],metadatas=[{'title':'用户导入'},None],embeddings=[[3.,4.],[4.,3.]])
            kb=KnowledgeBase.__new__(KnowledgeBase);kb._client=client;kb._open_cosine_collection()
            self.assertEqual(kb._collection.metadata['hnsw:space'],'cosine')
            rows=kb._collection.get(ids=['imported'],include=['documents','metadatas','embeddings'])
            self.assertEqual(rows['documents'],['用户导入知识'])
            self.assertEqual(rows['metadatas'],[{'title':'用户导入'}])
            self.assertEqual(rows['embeddings'].tolist(),[[3.,4.]])
            query=kb._collection.query(query_embeddings=[[6.,8.]],n_results=1)
            self.assertAlmostEqual(query['distances'][0][0],0.,places=5)
            kb._collection.add(ids=['new'],documents=['new doc'],embeddings=[[1.,0.]])
            kb._open_cosine_collection()
            self.assertEqual(kb._collection.count(),3)
            self.assertEqual(old.count(),2)
            kb._collection.delete(ids=['imported'])
            kb._open_cosine_collection()
            self.assertEqual(kb._collection.get(ids=['imported'],include=[])['ids'],[])

    def test_fresh_collection_cosine_and_wrong_space_fails(self):
        with tempfile.TemporaryDirectory() as tmp:
            client=chromadb.PersistentClient(path=tmp,settings=Settings(anonymized_telemetry=False))
            kb=KnowledgeBase.__new__(KnowledgeBase);kb._client=client;kb._open_cosine_collection()
            self.assertEqual(kb._collection.metadata['hnsw:space'],'cosine')
        with tempfile.TemporaryDirectory() as tmp:
            client=chromadb.PersistentClient(path=tmp,settings=Settings(anonymized_telemetry=False))
            client.get_or_create_collection(KnowledgeBase.COLLECTION_NAME,metadata={'hnsw:space':'l2'})
            kb=KnowledgeBase.__new__(KnowledgeBase);kb._client=client
            with self.assertRaises(RuntimeError):kb._open_cosine_collection()

class ExactSearchRecoveryTests(unittest.TestCase):
    def test_hnsw_failure_uses_existing_vectors_and_preserves_sources(self):
        from unittest.mock import Mock, patch
        collection = Mock()
        collection.count.return_value = 2
        collection.query.side_effect = RuntimeError('Cannot return the results in a contigious 2D array. Probably ef or M is too small')
        collection.get.return_value = {'ids': ['orthogonal', 'matching'], 'embeddings': [[0., 1.], [1., 0.]],
            'documents': ['不相关', '推广目标'], 'metadatas': [{'title': '其他'}, {'title': '营销', 'source_url': 'https://example.com'}]}
        kb = KnowledgeBase.__new__(KnowledgeBase)
        kb._collection = collection
        with patch('chromadb.utils.embedding_functions.DefaultEmbeddingFunction', return_value=lambda texts: [[1., 0.]]):
            result = kb.search('推广目标', 5)
        self.assertEqual([x['id'] for x in result], ['matching', 'orthogonal'])
        self.assertEqual(result[0]['score'], 1)
        self.assertEqual(result[0]['source_url'], 'https://example.com')
        collection.query.assert_called_once_with(query_texts=['推广目标'], n_results=2)
        collection.upsert.assert_not_called()
        collection.delete.assert_not_called()

    def test_unrelated_storage_failure_is_not_masked(self):
        from unittest.mock import Mock
        kb = KnowledgeBase.__new__(KnowledgeBase)
        kb._collection = Mock()
        kb._collection.count.return_value = 2
        kb._collection.query.side_effect = RuntimeError('storage unavailable')
        with self.assertRaisesRegex(RuntimeError, 'storage unavailable'):
            kb.search('推广目标')
        kb._collection.get.assert_not_called()
