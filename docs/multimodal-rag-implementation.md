# 工作台多模态 RAG

已接入商家创意工作台的创意对话。实现形式为图片/视频转文字后的检索增强，
不是 CLIP 图片向量检索，也不处理音频。悬浮 Agent 聊天的 RAG 开关不影响本链路。

## 实际调用链

1. 商家提交带素材的需求，原有 Seed 图片理解/视频抽样帧理解任务完成。
2. 理解结果保存在 MySQL `merchant_studio_jobs.result`，素材当前版本指向 `analysis_job_id`。
3. 工作台 Worker 每轮读取当前环境、当前品牌已完成理解的素材：当前附件优先，最多最近 100 个。
   浏览器不能传入知识库、品牌作用域或检索结果。
4. Python `/studio/generate` 将这些图片描述和视频帧描述分块，在
   Chroma `studio_media_caption_cosine_v1` 幂等写入文本向量。旧素材无需重新调用理解模型，首次检索自动补索引。
5. 用本轮问题、商品描述和 idea 做向量检索，最多返回 5 个片段。
   过滤器只允许返回本轮数据库快照对应的精确 chunk ID，历史旧索引和其他品牌不能进入结果。
6. DeepSeek 接收 `retrieved_media`，生成草稿并返回实际参考的 `source_ids`。
   服务端拒绝不在检索结果中的来源；Koa 再校验品牌、素材版本和理解任务。
7. 对话结果保留 `media_rag.retrieved` 和 `media_rag.citations`；创意方案保存 `media_sources`。
   页面显示已引用/未引用候选、原素材、视频时间点、未核验宣称与不确定信息。

## 配置与边界

- 默认启用，不需要新模型密钥或新增 SQL 迁移；复用现有素材理解结果和 Chroma 配置。
- 默认 `STUDIO_MEDIA_RAG_STORAGE=local`，使用 `CHROMA_PERSIST_DIRECTORY` 的本地持久化索引。
  多实例部署使用 `server` 模式和 `CHROMA_HOST/PORT`，服务器不可用时明确报错，不自动切换索引。
- 复用当前 Chroma 默认文本 embedding 模型，不宣称中文召回质量已提高。
- `STUDIO_MEDIA_RAG_MIN_SCORE=0.25` 为可配置余弦相似度下限，不是准确率或证据充足概率。
- `media_rag.status` 为 `empty`、`no_match`、`retrieved`；没有内部素材上下文的旧调用返回 `not_requested`。
- 有候选不代表模型实际引用；引用由 `source_ids` 单独记录，不能把素材来源当成功效证明。
- 没有素材或没有命中正常生成文字草稿；索引/embedding/检索故障返回 503，生成模型不被调用。
- 文本 Worker 既有恢复机制允许有限重试；没有添加图片或视频付费提交重试。
- 撤权后禁止读取对话与原素材；Worker 生成前及提交结果前均检查品牌权限。
- 旧索引片段当前保留在磁盘，但不在本轮授权 ID 集合内就不能召回。物理清理属于后续运维工作。

## 验证

```bash
.venv/bin/python -m unittest tests.test_studio tests.test_multimodal_rag -v
PATH=/opt/homebrew/opt/node@22/bin:$PATH npm --prefix apps/mini-ad-wall/server run build
PATH=/opt/homebrew/opt/node@22/bin:$PATH node --test apps/mini-ad-wall/server/tests/multimodal-rag.test.cjs apps/mini-ad-wall/server/tests/studio.test.cjs
PATH=/opt/homebrew/opt/node@22/bin:$PATH npm --prefix apps/mini-ad-wall/client run build
```

Python 测试使用真实持久化 Chroma、固定向量和模型响应，覆盖跨品牌/环境隔离、
旧版本排除、重复入库、视频时间点、历史素材进入生成提示词、伪造引用及检索故障。
Node 测试使用临时真实 MySQL，覆盖素材查询、最新版本、失败任务排除、浏览器注入拒绝、
Worker 持久化引用与权限撤销。没有因此证明线上模型生成质量或商业效果。

2026-10-14 验证结果：12 个 Python 专项测试、2 个真实 MySQL 生命周期测试、
前后端构建均通过。`docs/validation/multimodal-rag/browser.json` 记录页面引用、
素材预览、视频 1.5 秒定位及 320/390/768 宽度检查；使用界面测试数据，不提交真实业务或付费模型请求。
`real-embedding-smoke.json` 是真实本地文本 embedding 的中文素材小样本检索，
不是召回率评测。当前本机服务已重载，Python 与 Koa 的 capabilities 均确认新链路。
当前业务数据库尚无已完成理解的素材，完整真实素材/在线生成验收需上传素材后进行。

## 回退

回退本次应用代码并重启 Python、Koa 和 studio Worker 即可恢复原有附件理解流程。
没有修改原有知识库集合或 SQL 表结构；新增向量集合可保留供再次启用。
