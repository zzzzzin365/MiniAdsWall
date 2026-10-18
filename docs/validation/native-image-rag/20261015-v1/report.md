# 原生图像向量检索：实现与验证

日期：2026-10-15。状态：本地代码、索引、API与工作台Worker已切换到原生图像检索。默认模式为`native`；原描述向量实现保留为显式回退。没有提交或推送代码。

现在的检索：**原图像素／视频抽样帧 → Chinese-CLIP图像编码器 → 512维归一化向量 → 独立Chroma索引**；中文问题通过同一模型的文本编码器进入同一空间。图片描述只作为回答上下文，不参与图像向量计算。通用文本文档仍使用原文本检索。

模型为`OFA-Sys/chinese-clip-vit-base-patch16`，固定revision `36e679e65c2a2fead755ae21162091293ad37834`。CPU运行，固定Torch2.14.1、Transformers4.46.3、Pillow12.3.0和tokenizers0.20.3，兼容现有Chroma0.5.23。[模型官方用法](https://huggingface.co/docs/transformers/model_doc/chinese_clip)。

## 验证结果

| 验证项 | 结果 |
| --- | --- |
| Python专项测试 | 23项通过；包含原描述模式的显式回退回归 |
| 真实MySQL工作台生命周期 | 2项通过，未跳过；含授权、引用、Seed失败后保留向量与旧任务重建 |
| 服务端TypeScript构建 | 通过 |
| 安装依赖检查 | `uv pip check`通过，版本兼容 |
| 原图真实编码 | 既有外部目录6张原图，Chroma存储向量均为512维；重新读原图编码与已存向量一致 |
| 图片描述移除 | 清空图片描述并替换文章摘要后，三个固定中文查询的图像结果ID与分数均不变 |
| 模型文本编码正确性 | 独立文本编码与模型完整图文forward的文本输出一致，最大差值0.0 |
| ASGI真实模型接口 | `/studio/embed`和`/studio/reference-search`均200；使用真实模型，无付费API |
| 已运行服务 | Koa3001 → Python8000 → Chinese-CLIP → Chroma，真实授权HTTP检索200 |
| 文章／图片／混合三种模式 | 均200；分别报告text-vector、chinese-clip-image、native-image-plus-text方法 |
| 原图预览 | 200，预览文件hash与检索图片来源hash一致 |
| 未授权请求 | Koa搜索401；Python图像编码401 |
| 现有素材迁移 | 为当前已授权4份素材的8个画面补建本地图像向量，仅为业务迁移，未纳入实验数据 |
| 本次验证付费模型请求 | 0；没有重新提交Seed理解任务或生成广告 |

“汽车展示图”返回的首位为`zhihu-components`；“紫色卡通猫头鹰”返回首位为`xhs-duo-2`。全部三个查询及候选分数保存在`smoke.json`，未删去不理想的检索结果。

## 范围与限制

这是功能验证，不是新的质量／速度对照实验。6张图、3个查询不能证明召回率、P95或最终答案质量提高；原来的描述检索实验成绩不直接适用于Chinese-CLIP。56张新手串图仍未用于此次检索评测。

品牌迁移只在本地对像素进行编码，不发送到外部模型，也不作为质量测试语料。图像向量匹配不代表Seed已理解画面；理解失败仍保留失败状态，生成接口使用`visual_indexed`而非`seed_understood`，避免把检索命中当作细节证据。

视频仅检索抽样帧，无音频；本轮提供中文文搜图，不包含图搜图输入接口。原生图像余弦阈值尚未做质量校准。Docker依赖与模型预载配置已同步，本轮未构建镜像或验证生产部署。

## 实现与证据

- `core/image_embeddings.py`：固定模型与CPU编码器；像素归一化向量，显式embeddings写入及query_embeddings查询。
- `core/multimodal_rag.py`：授权品牌／环境／版本／图片hash／视频帧绑定；原生图像集合独立于原384维文字集合。
- `mcp/external_marketing.py`：外部原图搜索、路径边界及文章/图像排名融合。
- `api/studio.py`：新本地图像编码端点、原生搜索、向量不进入回答模型、引用与理解状态。
- `apps/mini-ad-wall/server/services/merchant/studio/worker.ts`：独立保存像素向量及Seed理解；保留失败状态。
- `apps/mini-ad-wall/server/services/merchant/studio/reindex.ts`：授权素材向量重建，不调用Seed。
- `contracts/merchant/studio/v1`：模型、revision、向量维数、hash、帧时间、引用格式的共享约束。
- `smoke.json`：真实模型编码、描述移除检查、逐查询结果、源码hash。
- `runtime.json`：实际运行服务、鉴权、预览hash、迁移数量与兼容依赖版本。

配置、初始化和回退说明见 `docs/native-image-rag.md`。
