# Seedream 图片生成

确认创意方案后点击「生成图片素材」，Koa 创建持久任务，worker 调用 FastAPI `/studio/image`，使用现有 `ARK_API_KEY` 调用 `POST /api/v3/images/generations`。模型固定 `doubao-seedream-5-0-flash-260915`，单图、2K、PNG、base64 返回、开启水印；不发送此模型不支持的组图参数。图片落本地素材存储并创建 output，可预览、下载及走现有审核流程。

上传和素材理解不会触发图片生成。当前入口是明确的生成按钮，不从自由聊天文本猜测付费意图。创意必须先确认。未知结果或 worker 中断标记 submission_unknown，不自动重新提交。

官方参考：https://docs.volcengine.com/docs/ark/image-generation-api?lang=zh
模型参考：https://docs.volcengine.com/docs/ark/seedream-4-0-5-0?lang=zh
用户原链接只取得控制台外壳，参数依据官方同接口文档核验。

验证：前后端生产构建通过；`PYTHONPATH=. .venv/bin/python tests/test_seedream_adapter.py` 两项 mock 检查通过。未调用真实付费模型，未完成浏览器和数据库端到端验收。需重启 FastAPI、Koa 与 studio worker 才加载后端改动。
