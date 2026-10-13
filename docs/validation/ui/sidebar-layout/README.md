# 左右工作台布局验收

保留原有 body 蓝色渐变、配色和卡片风格，将账号凭证表单和模块导航放到左侧。桌面左右布局，700px 以下导航折到顶部。

导航：广告管理、素材管理（已有素材管理 / 素材生成工作台）、数据分析。沿用现有广告素材卡片、创意工作台和统计图表，已有素材管理的范围为已有广告素材。

浏览器检查使用拦截 API 的 UI 数据，不验证业务服务或模型生成；没有业务写入或付费生成。检查详情见 results.json，截图在当前目录。客户端 TypeScript/Vite 构建通过。

运行：

```sh
NODE_PATH=/Users/admin/.cache/codex-runtimes/codex-primary-runtime/dependencies/node/node_modules /opt/homebrew/opt/node@22/bin/node docs/validation/ui/sidebar-layout/check.cjs
```

默认页面：http://127.0.0.1:5173。兼容 `?studio=1`、`#creatives`、`?marketing=1` 与 `#data-analysis` 入口。
