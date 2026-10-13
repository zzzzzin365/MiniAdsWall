# 商家营销创意工作台验证报告

日期：2026-10-13；本地macOS/Node22/MySQL8.4。所有新增检查在可丢弃的隔离MySQL中执行，没有修改工作数据库，没有提交新的付费视频。

| 范围 | 结果 | 证据与限制 |
|---|---|---|
| Python固定模型/能力拒绝/Schema/格式失败/CPC Decimal计算 | 5/5通过 | python-test.log；httpx MockTransport，不是所有模型均实调 |
| HTTP→业务事务→MySQL→Worker→产物/审核/草稿 | 通过 | mysql-http-test.log；两个独立Koa进程竞争同幂等键 |
| 图片原件上传/解码/品牌隔离/私有下载 | 通过 | 真实ffmpeg生成测试PNG，真实HTTP与MySQL；不证明视觉理解 |
| 未授权视频/图片请求/旧revision/跨品牌/撤权 | 通过 | 新接口返回明确拒绝；供应商调用计数有断言 |
| Seedance响应丢失/Worker崩溃/已知task查询/产物转存与审核 | 协议通过 | provider与下载函数模拟，真实MySQL；不证明供应商实调/真实公网下载 |
| DeepSeek真实文本调用 | 通过，10.6秒/3方向 | deepseek-live.json；独立调用studio生成函数，非浏览器全链实时模型 |
| Chrome桌面1280/移动320 | 通过 | browser.json、desktop.png、mobile.png；真实Koa/MySQL/Worker，文本供应商返回复用实调fixture；未点击付费生成 |
| 前后端构建、Python语法、diff空白 | 通过 | npm server build、client build、py_compile、git diff --check |
| 全量服务端回归首次 | 65通过/1失败/3跳过 | 69项计数含子测试；营销聚合CP04计数4!=6，不能称全库绿色 |
| 聚合模块定向复跑 | 8/8通过 | 未修改聚合代码；首次失败与测试并行时序可能相关，根因尚未证明 |

检查命令：

```sh
.venv/bin/python -m unittest tests.test_studio -v
PATH=/opt/homebrew/opt/node@22/bin:$PATH npm --prefix apps/mini-ad-wall/server run build
PATH=/opt/homebrew/opt/node@22/bin:$PATH node --test apps/mini-ad-wall/server/tests/studio.test.cjs
PATH=/opt/homebrew/opt/node@22/bin:$PATH npm --prefix apps/mini-ad-wall/client run build
```

浏览器验证脚本保存为browser-test.cjs（需设置PLAYWRIGHT_MODULE及测试base URL，运行测试沙箱时复用fixture）；数据库测试已在server/tests/studio.test.cjs中固化。测试中的运营凭据为固定测试值，与任何真实凭据无关。

仍未验收：直接图片/音视频理解（当前模型能力不支持）、图片生成（当前指定模型不支持）、真实工作台Seedance提交/公网首帧读取/下载、30例人工创意评测、真实服务计费/投放、多节点共享对象存储。供应商历史接入成功不能替代本工作台验收。


## 现有模块集成与UI验收

本次改动仅前端模块组织/视觉及设计说明，无数据库/模型协议改动。前端build通过。embedded-browser.json验证：仅两个原标签、取消独立入口、标签切换保留未提交表单、标签内创意生成、旧studio链接兼容、320px无横向溢出。桌面与窄屏截图为embedded-desktop.png/embedded-mobile.png。创意视图隐藏列表专用筛选/报表及重复浮动助手，已有素材浏览保留。测试为隔离MySQL/模拟供应商，不包含付费生成。
