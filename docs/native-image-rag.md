# 原生图像向量检索

2026-10-15 改造：素材与外部案例图使用 Chinese-CLIP 原图编码，替代图片描述向量检索。通用文本文档仍走原文本知识库。旧实验报告保留原实现与原成绩，不用于声明此次改造的收益。

## 真实调用链

1. `/studio/embed` 在本机读取请求中的原图/视频抽样帧像素，用固定版本 Chinese-CLIP 得到归一化512维图像向量，不调用Seed或其他付费模型。
2. 工作台Worker先计算图像向量，再调用原有Seed画面理解，并在任务结束时持久化结果。Seed失败时任务仍标为失败，但已完成的图像向量保存在任务result，供检索使用；不会伪造成功理解或画面描述。
3. Koa从当前品牌、环境和素材当前版本对应的任务读取向量，校验模型、维数、范数、原图hash、帧号与时间点；浏览器不能传入知识集合或向量。
4. 问题通过同一Chinese-CLIP文本编码器得到512维查询向量，在独立Chroma集合中查询真实图像向量。写入使用`embeddings`，查询使用`query_embeddings`，不调用Chroma默认文本embedding。
5. 原有描述仅供DeepSeek生成时参考。没有Seed描述时标记`understanding_available=false`、`visual_indexed`；相似图命中不能当作细节或商品功效的证据。
6. 外部参考搜索 `/studio/reference-search` 默认`mode=images`，同样检索原图向量。生成草稿时，图像结果和文章文本结果按排名融合，两个模型的余弦分数不直接相加。
7. 引用仍校验素材版本、品牌权限、图像hash、帧时间与合法来源ID。

模型：`OFA-Sys/chinese-clip-vit-base-patch16`，固定revision `36e679e65c2a2fead755ae21162091293ad37834`，CPU运行。图片和中文查询共享512维空间，分别编码后进行余弦匹配。[官方用法](https://huggingface.co/docs/transformers/model_doc/chinese_clip)。

集合：`external_image_chinese_clip_v1`（外部原图）、`studio_media_image_chinese_clip_v1`（授权品牌图片/视频帧）。原有384维文本集合保留，不混入图像向量。没有新增SQL表；向量持久化在任务JSON中，Chroma为可重建索引。

## 安装与初始化

```sh
uv pip install --python .venv/bin/python -r requirements-vision.txt
HF_HUB_DISABLE_XET=1 .venv/bin/python scripts/setup_image_retrieval.py
.venv/bin/python scripts/index_image_references.py
```

默认依赖清单已包含`requirements-vision.txt`。主API的Docker构建同步预载固定版本权重；本轮没有构建或验证容器镜像。

默认`STUDIO_MEDIA_RAG_MODE=native`，Python与Koa必须保持一致。设置`caption`可显式回退原描述检索；原生检索失败不会静默降级成文本检索。

在线请求仅加载本地已下载模型，不临时下载权重。可用`IMAGE_EMBEDDING_MODEL_PATH`指定固定版本模型的本地镜像，`IMAGE_EMBEDDING_THREADS`默认4。`STUDIO_IMAGE_RAG_MIN_SCORE`为品牌原生图像余弦门槛，默认0，尚未校准，不能当准确率。原文本门槛不直接套到新空间。

旧品牌素材理解任务缺少原图向量时会明确报`native_media_reindex_required`。已有素材可按明确品牌、环境、身份重建：

```sh
PATH=/opt/homebrew/opt/node@22/bin:$PATH npm --prefix apps/mini-ad-wall/server run build
# 按实际品牌/环境/运营身份替换参数；只调本地embed，不调用付费Seed。
node apps/mini-ad-wall/server/dist/scripts/reindex-studio-images.js BRAND ENVIRONMENT PRINCIPAL
```

重建会重新校验授权、当前素材版本和任务指针，保留原有理解内容和任务成功/失败状态。每批最多100素材，超出明确失败，不静默遗漏。

固定依赖版本见 `requirements-vision.txt`。Transformers与现有Chroma共用兼容的tokenizers版本；文本特征按官方forward的CLS投影实现，并与完整图文forward输出核对。

## 验证与边界

- 确定性真实Chroma测试：显式像素向量写入/查询、同描述不同图不合并、去掉/改错描述不改变图像匹配、跨品牌/环境/版本过滤、模型不匹配拒绝、没有向量时不冒充原生检索。
- 真实MySQL工作台生命周期：像素向量持久化、Seed失败后保留向量、不冒充成功理解、旧任务重建、权限拒绝和引用校验。
- `scripts/index_image_references.py`用归档的真实外部原图运行模型，保存512维向量与中文查询结果；清空所有图片描述后，结果ID和分数仍应一致。
- 这些验证证明原生图像检索实际运行，不等于召回率、速度或最终答案质量提高。旧四组实验没有测试此次Chinese-CLIP，后续应重新固定数据和问题做独立对照。
- 视频仍是最多8帧的图像检索，不含音频；本轮未增加图搜图输入接口。
