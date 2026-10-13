# Seedance 1.0 Pro Fast 视频 API

2026-10-13 使用 opencli 浏览器读取指定原文及查询接口。模型：doubao-seedance-1-0-pro-fast-251015。

- 原文：https://ark.volcengine.com/region:cn-beijing/docs/ark/create-video-generation-task-api?lang=zh
- 查询：https://ark.volcengine.com/region:cn-beijing/docs/ark/get-video-generation-task-api?lang=zh
- POST /generate/video：prompt 必填；image_url 可选（HTTPS 首帧图片）；resolution 默认480p，可选720p/1080p；duration 默认5秒，范围2–12；ratio 默认9:16；watermark 默认true。
- GET /generate/video/{task_id}：返回 queued/running/succeeded/failed/cancelled 等状态。成功时 content.video_url 为生成结果。
- 两个接口沿用 AGENT_SERVICE_TOKEN 内部认证，仅供可信服务调用。没有开放多用户 Koa/前端入口；接入前须增加任务归属校验。
- 视频模型独立读取 SEEDANCE_MODEL，不改变 ARK_MODEL 文本模型；复用 ARK_API_KEY、ARK_BASE_URL。
- 不支持首尾帧、参考音频、视频输入；不声称生成音频。字幕/口播可后期添加。
- 创建任务不自动重试。超时可能已计费/已创建，先核查方舟任务列表。请求 ID 仅作跟踪，不宣称上游幂等。
- CLI create 强制保存任务收据；已有收据拒绝覆盖。发生提交异常也保留 submitting 收据供核查，不直接再次提交。
- 任务记录保留7天，视频 URL 有效24小时。当前返回 URL，未自动转存或发布到广告。

命令：

```bash
.venv/bin/python scripts/seedance_video.py create --prompt '商品广告镜头：纯白背景上的红色陶瓷杯，缓慢推进，无文字' --duration 5 --receipt data/seedance/task-001.json
.venv/bin/python scripts/seedance_video.py query '第一条命令返回的任务ID'
```
