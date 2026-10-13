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
