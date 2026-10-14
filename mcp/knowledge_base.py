"""
RAG 知识库 —— 基于 ChromaDB 的真实检索实现。

功能：
  1. 文档导入：将文本切片后存入 ChromaDB（自动生成 Embedding）
  2. 语义检索：根据 query 从知识库中检索最相关的文档片段
  3. 与 MCP 工具框架集成：作为 knowledge_search 工具的真实 handler

ChromaDB 在这里的角色：
  - memory/ 中用于存储对话记忆（情景记忆 + 用户画像）
  - 这里用于存储知识库文档（RAG 检索）
  两者是不同的 collection，互不干扰。
"""
import asyncio
import hashlib
import logging
from typing import Any, Dict, List, Optional

import chromadb

from mcp.advertising_knowledge import ARTICLE_KNOWLEDGE, LEGACY_ARTICLE_TITLES

logger = logging.getLogger(__name__)


class KnowledgeBase:
    """
    基于 ChromaDB 的 RAG 知识库。

    ChromaDB 内置了 Embedding 模型（all-MiniLM-L6-v2），
    调用 add() 时自动生成向量，query() 时自动做语义匹配。
    不需要额外调用 Anthropic Embeddings API。
    """

    LEGACY_COLLECTION_NAME = "knowledge_base"
    COLLECTION_NAME = "knowledge_base_cosine_v1"

    def __init__(
        self,
        chroma_host: str = "localhost",
        chroma_port: int = 8000,
        chroma_path: str = "./data/chroma",
    ):
        from chromadb.utils.embedding_functions import ONNXMiniLM_L6_V2
        # CoreML's provider can stall parallel query embeddings on macOS.
        # CPU uses the same MiniLM model/vectors without compiling CoreML graphs.
        self._embedding_function = ONNXMiniLM_L6_V2(preferred_providers=['CPUExecutionProvider'])
        # 优先连接独立 ChromaDB 服务（服务端内置 embedding 模型，客户端无需下载）
        self._use_server = False
        try:
            # HttpClient 默认也会初始化 ChromaDB telemetry；显式关闭避免 posthog 兼容性错误日志。
            self._client = chromadb.HttpClient(
                host=chroma_host,
                port=chroma_port,
                settings=chromadb.Settings(anonymized_telemetry=False),
            )
            self._client.heartbeat()
            self._use_server = True
            logger.info(f"知识库 ChromaDB 已连接: {chroma_host}:{chroma_port}")
        except Exception:
            logger.info(f"知识库 ChromaDB 服务不可用，使用本地模式: {chroma_path}")
            self._client = chromadb.PersistentClient(
                path=chroma_path,
                settings=chromadb.Settings(anonymized_telemetry=False),
            )

        # 使用服务端时不传 embedding_function，让服务端处理
        # 本地模式时也不传，使用 ChromaDB 默认的（会触发模型下载）
        self._open_cosine_collection()

        # 启动时幂等写入广告领域默认知识，避免旧客服默认文档污染 AdsAgent RAG。
        self._ensure_default_docs()

    def _open_cosine_collection(self) -> None:
        """Rebuild the immutable distance index, preserving imported vectors."""
        self._collection = self._client.get_or_create_collection(
            name=self.COLLECTION_NAME,
            metadata={"description": "MiniAdsWall Agent RAG 知识库", "hnsw:space": "cosine"},
            **({'embedding_function': self._embedding_function} if hasattr(self, '_embedding_function') else {}),
        )
        metadata = self._collection.metadata or {}
        if metadata.get("hnsw:space") != "cosine":
            raise RuntimeError("知识库索引不是 cosine，不能直接修改已有索引距离")
        # Separate completion record: Chroma 0.5 rejects modifying hnsw:space
        # metadata even when its value is unchanged. Do not reimport deletions.
        migrations = self._client.get_or_create_collection("knowledge_base_migration_state")
        if migrations.get(ids=[self.COLLECTION_NAME], include=[])["ids"]:
            return
        from chromadb.errors import InvalidCollectionException
        try:
            legacy = self._client.get_collection(self.LEGACY_COLLECTION_NAME)
        except InvalidCollectionException:
            migrations.upsert(ids=[self.COLLECTION_NAME], embeddings=[[0.]], documents=["complete: no legacy collection"])
            return  # No existing knowledge to migrate.
        # Keep the old collection intact. Interrupted migration resumes by ID.
        expected = legacy.count()
        for offset in range(0, expected, 100):
            rows = legacy.get(limit=100, offset=offset, include=["documents", "metadatas", "embeddings"])
            existing = set(self._collection.get(ids=rows["ids"], include=[])["ids"])
            missing = [i for i, doc_id in enumerate(rows["ids"]) if doc_id not in existing]
            if not missing:
                continue
            values = {"ids": [rows["ids"][i] for i in missing],
                      "embeddings": [rows["embeddings"][i] for i in missing]}
            if rows["documents"] is not None:
                values["documents"] = [rows["documents"][i] for i in missing]
            if rows["metadatas"] is not None:
                values["metadatas"] = [rows["metadatas"][i] for i in missing]
            self._collection.upsert(**values)
            copied = self._collection.get(ids=rows["ids"], include=[])
            if set(copied["ids"]) != set(rows["ids"]):
                raise RuntimeError("知识库 cosine 索引迁移不完整")
        if legacy.count() != expected:
            raise RuntimeError("迁移过程中旧知识库发生变化，请停止旧版本写入后重试")
        migrations.upsert(ids=[self.COLLECTION_NAME], embeddings=[[0.]], documents=["complete"])

    # ── 文档管理 ──────────────────────────────────────────────────────────────

    def add_documents(self, documents: List[Dict[str, str]]) -> int:
        """
        批量导入文档到知识库。

        documents 格式: [{"title": "...", "content": "..."}, ...]
        长文档会自动切片（每片 500 字）。
        """
        ids, docs, metas = [], [], []

        for doc in documents:
            title   = doc.get("title", "")
            content = doc.get("content", "")
            chunks  = self._chunk_text(content, chunk_size=500)

            for i, chunk in enumerate(chunks):
                doc_id = hashlib.md5(f"{title}_{i}_{chunk[:50]}".encode()).hexdigest()
                ids.append(doc_id)
                docs.append(chunk)
                metas.append({"title": title, "chunk_index": i, "total_chunks": len(chunks)})

        if ids:
            # ChromaDB 会自动生成 Embedding
            self._collection.add(ids=ids, documents=docs, metadatas=metas)
            logger.info(f"知识库导入 {len(ids)} 个文档片段")

        return len(ids)

    def search(self, query: str, top_k: int = 5) -> List[Dict[str, Any]]:
        """
        语义检索：根据 query 返回最相关的文档片段。

        ChromaDB 内部自动将 query 转为向量，与存储的文档向量做余弦相似度匹配。
        """
        count = self._collection.count()
        if not count:
            return []
        top_k = min(top_k, count)
        try:
            results = self._collection.query(query_texts=[query], n_results=top_k)
        except RuntimeError as exc:
            # Chroma/HNSW can fail to assemble k neighbours after updates/deletes.
            # For a bounded corpus, search the actual stored vectors exactly.
            if 'contigious 2D array' not in str(exc) and 'contiguous 2D array' not in str(exc):
                raise
            if count > 10000:
                raise  # Do not perform an unbounded full scan in the request path.
            logger.warning('HNSW query failed; using exact cosine search over %s knowledge chunks', count)
            results = self._exact_cosine_query(query, top_k, count)

        items = []
        if results["documents"] and results["documents"][0]:
            for chunk_id, doc, meta, dist in zip(
                results["ids"][0],
                results["documents"][0],
                results["metadatas"][0],
                results["distances"][0],
            ):
                items.append({
                    "id":       chunk_id,
                    "title":    meta.get("title", ""),
                    "content":  doc,
                    "score":    round(1.0 - dist, 4),  # cosine 距离为 1-cos，恢复余弦相似度；不是正确概率
                    "chunk":    meta.get("chunk_index", 0),
                    "source_url": meta.get("source_url", ""),
                    "source_title": meta.get("source_title", ""),
                })

        return items

    def _exact_cosine_query(self, query: str, top_k: int, count: int) -> dict:
        import numpy as np
        from chromadb.utils.embedding_functions import DefaultEmbeddingFunction
        # This collection is created with Chroma's default embedding function.
        embed = getattr(self, '_embedding_function', None) or DefaultEmbeddingFunction()
        vector = np.asarray(embed([query])[0], dtype=float)
        norm = np.linalg.norm(vector)
        if not norm or not np.isfinite(vector).all():
            raise ValueError('invalid_query_vector')
        candidates = []
        for offset in range(0, count, 500):
            rows = self._collection.get(limit=min(500, count-offset), offset=offset,
                                        include=['documents', 'metadatas', 'embeddings'])
            for i, chunk_id in enumerate(rows['ids']):
                stored = np.asarray(rows['embeddings'][i], dtype=float)
                denominator = norm * np.linalg.norm(stored)
                if stored.shape != vector.shape or not denominator or not np.isfinite(stored).all():
                    raise ValueError('invalid_knowledge_vector')
                score = float(np.clip(np.dot(vector, stored) / denominator, -1, 1))
                candidates.append((score, chunk_id, rows['documents'][i], rows['metadatas'][i] or {}))
        candidates.sort(key=lambda item: (-item[0], item[1]))
        selected = candidates[:top_k]
        return {'ids': [[x[1] for x in selected]], 'documents': [[x[2] for x in selected]],
                'metadatas': [[x[3] for x in selected]], 'distances': [[1-x[0] for x in selected]]}

    @property
    def doc_count(self) -> int:
        return self._collection.count()

    # ── MCP 工具 handler ─────────────────────────────────────────────────────

    async def search_handler(self, params: Dict[str, Any], context: Any) -> List[Dict]:
        """
        作为 MCP 工具的 handler 注册。

        MCPToolManager.register(Tool(
            name="knowledge_search",
            handler=kb.search_handler,
            ...
        ))
        """
        query = params.get("query", "")
        top_k = params.get("top_k", 5)
        # Chroma and embedding calls are synchronous; keep them off the event
        # loop so the caller's retrieval deadline can cancel the await.
        return await asyncio.to_thread(self.search, query, top_k=top_k)

    # ── 内部方法 ──────────────────────────────────────────────────────────────

    def _chunk_text(self, text: str, chunk_size: int = 500) -> List[str]:
        """将长文本按 chunk_size 切片，保留语义完整性（按句号/换行切分）。"""
        if len(text) <= chunk_size:
            return [text] if text.strip() else []

        chunks = []
        current = ""
        # 按句子切分
        sentences = text.replace("\n", "。").split("。")
        for sent in sentences:
            sent = sent.strip()
            if not sent:
                continue
            if len(current) + len(sent) + 1 > chunk_size:
                if current:
                    chunks.append(current)
                current = sent
            else:
                current = f"{current}。{sent}" if current else sent

        if current:
            chunks.append(current)

        return chunks

    def _default_docs(self) -> List[Dict[str, str]]:
        """广告运营场景默认知识文档。"""
        return [
            {
                "title": "广告排序规则",
                "content": (
                    "Mini Ad Manager 使用出价和点击量综合排序。"
                    "当前示例规则为 Pricing + (Pricing x Clicks x 0.42)。"
                    "高出价可以提升基础分，但点击量会放大有效广告的排序优势。"
                    "高出价低点击广告不应盲目继续加价，应优先检查标题、素材和卖点。"
                ),
            },
            {
                "title": "出价优化原则",
                "content": (
                    "出价优化需要结合点击表现判断。"
                    "高点击且出价适中的广告可以小幅加价观察排名变化。"
                    "低出价但点击高的广告可能具备素材或文案优势，可以作为放量候选。"
                    "高出价但点击低的广告应先优化素材和标题，再考虑预算调整。"
                ),
            },
            {
                "title": "素材与 A/B 测试",
                "content": (
                    "广告素材测试应一次只改变一个核心变量，例如标题、封面、视频开头或出价。"
                    "每组测试应保留对照组，避免同时修改多个变量导致无法归因。"
                    "没有视频素材或素材数量过少的广告，应先补齐素材再判断投放效果。"
                    "观察点击差异时应设置固定时间窗口或最低样本量。"
                ),
            },
            {
                "title": "广告文案优化",
                "content": (
                    "广告标题和文案应突出目标人群、核心利益点、差异化卖点和行动引导。"
                    "文案可以从利益点型、痛点型、场景型三个方向生成候选版本。"
                    "不要使用无法证明的绝对化承诺，也不要通过误导性标题换取点击。"
                    "点击率低时，应同时检查标题吸引力、素材开头和目标人群匹配度。"
                ),
            },
            {
                "title": "数据看板解读",
                "content": (
                    "广告看板应优先关注出价分布、点击热度、排名与出价关系、素材数量与点击表现。"
                    "总点击量能反映整体互动规模，但不能单独代表转化效果。"
                    "平均出价可以帮助判断预算强度，但需要结合点击表现分析是否有效。"
                    "视频数量和点击表现的关系可以作为素材测试方向。"
                ),
            },
            {
                "title": "广告运营风险边界",
                "content": (
                    "广告运营建议不能承诺点击率、转化率、收益或排名一定提升。"
                    "涉及真实预算、批量下线、删除素材或高风险行业广告时，需要人工或权限确认。"
                    "如果缺少曝光、转化、成本等指标，只能基于出价、点击和素材数据做保守分析。"
                    "不得建议虚假宣传、误导性标题或违规素材。"
                ),
            },
        ] + [
            {**doc, "content": (
                f"{doc['content']}\n外部业务参考；来源：{doc['source_title']} {doc['source_url']}"
            )}
            for doc in ARTICLE_KNOWLEDGE
        ]

    def _ensure_default_docs(self) -> None:
        """删除旧客服默认文档，并幂等写入广告默认知识文档。"""
        old_titles = ["退款政策", "订单查询", "账户安全", "技术故障排查", "会员与积分", "配送说明"]
        for title in old_titles:
            try:
                self._collection.delete(where={"title": title})
            except Exception as ex:
                logger.debug(f"删除旧默认知识失败: {title} {ex}")

        ids, docs, metas = [], [], []
        for doc in self._default_docs():
            title = doc["title"]
            chunks = self._chunk_text(doc["content"], chunk_size=500)
            for i, chunk in enumerate(chunks):
                doc_id = hashlib.md5(f"default_ads_{title}_{i}".encode()).hexdigest()
                ids.append(doc_id)
                docs.append(chunk)
                metas.append({
                    "title": title, "chunk_index": i, "total_chunks": len(chunks),
                    "source": "default_ads",
                    "knowledge_id": doc.get("knowledge_id", ""),
                    "source_url": doc.get("source_url", ""),
                    "source_title": doc.get("source_title", ""),
                })

        if ids:
            self._collection.upsert(ids=ids, documents=docs, metadatas=metas)
            # 仅替换上一版内置文章摘要的已知 ID，保留用户自行导入的同名文档。
            # 新条目写入成功后才清理旧摘要，避免写入失败时先丢失旧知识。
            self._collection.delete(ids=[
                hashlib.md5(f"default_ads_{title}_0".encode()).hexdigest()
                for title in LEGACY_ARTICLE_TITLES
            ])
            logger.info(f"广告默认知识已写入 {len(ids)} 个文档片段")

    def _load_default_docs(self) -> None:
        """兼容旧调用：导入广告运营场景默认知识文档。"""
        self._ensure_default_docs()
