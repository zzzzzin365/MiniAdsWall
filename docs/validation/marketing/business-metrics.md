# 业务指标验收记录

日期：2026-10-10。本次只验证本地实现；数据库采用隔离的 MySQL 8.4 临时实例，没有迁移真实主库、打开生产开关或部署。

## 结果

| 检查 | 结果 |
| --- | --- |
| Koa TypeScript 构建 | 通过 |
| React TypeScript / Vite 构建 | 通过；指标页按需加载 |
| 后端全量测试（含真实 MySQL、Python Worker、Chrome） | 59 项通过，0 失败，0 跳过 |
| Python 营销契约与归因测试 | 6 项通过 |
| git diff --check | 通过 |

日志：[Node 全量测试](business-metrics/node-tests.log)、[Python 测试](business-metrics/python-tests.log)。截图：[桌面](business-metrics/dashboard.png)、[手机](business-metrics/mobile.png)。

### 2026-10-11 同页整合复验

指标已移入“数据分析 → 投放效果”，删除工具栏独立入口。原有出价/排名/视频/点击图表放在同模块“广告概览”，切换不跳转页面，并保留查询、历史快照与估算结果。首次打开投放效果才加载对应组件；旧 `/?marketing=1` 链接选中广告管理内的投放效果。

本次前端构建通过；真实 Chrome 定向测试 1 项通过、无跳过，覆盖同页切换、四个原有图表保留、结果保留、真实指标接口、下载、旧链接兼容及 390px 手机无横向溢出。未重复运行未改动的后端全量/Python 套件，上表记录为 2026-10-10 的验证。

日志：[同页交互复验](business-metrics/embedded-browser-tests.log)；最新截图：[桌面](business-metrics/dashboard-embedded.png)、[手机](business-metrics/mobile-embedded.png)。

## 验证范围

- 固定样本 100 曝光、10 点击、2 订单、1000 分费用、5000 分净收入：CTR 10%、CVR 20%、CPA 500 分、CPC 100 分、CPM 10000 分、ROAS 5。
- 跨支付日期的点击归因、半开时间窗口、零分母、一点击多订单、超过 100% 的比值、精确金额计算、小概率估算。
- 费用来源缺失、双来源完整核对、覆盖区间拼接/缺口/失效、聚合落后时返回缺失原因，观察期结束后的完整状态。
- 退款后新报表更新净收入；原快照保持原值；内容被篡改时哈希校验失败。
- 品牌权限、运行环境和素材归属校验；12→13 增量迁移、中断恢复和重复执行。
- Chrome 实际查询、费用缺失显示、eCPM/LTV 假设计算、历史快照回查、JSON 下载；390px 手机宽度无横向溢出，无页面脚本异常。
- React 检查：表单显式提交、异步请求取消、组件卸载清理、按需加载、输入标签、状态及错误提示。

## 复现命令

```sh
PATH=/opt/homebrew/opt/node@22/bin:$PATH npm --prefix apps/mini-ad-wall/server run build
PATH=/opt/homebrew/opt/node@22/bin:$PATH npm --prefix apps/mini-ad-wall/client run build
PATH=/opt/homebrew/opt/node@22/bin:$PATH \
MARKETING_TEST_PLAYWRIGHT=/Users/admin/.cache/codex-runtimes/codex-primary-runtime/dependencies/node/node_modules/playwright \
MARKETING_TEST_CHROME='/Applications/Google Chrome.app/Contents/MacOS/Google Chrome' \
node --test --test-concurrency=2 apps/mini-ad-wall/server/tests/*.test.cjs
.venv/bin/python -m unittest tests.test_marketing_contracts tests.test_marketing_aggregation
git diff --check
```

## 未验证或未实现

生产数据量、真实收益、海量报表查询性能和 S1 全链路验收尚未验证。单次查询最多 31 天；大范围后台异步导出和 Agent 报表工具尚未实现。eCPM/LTV 仅按用户输入假设估算；没有新增预测模型、真实客户生命周期统计、外部 RTB、eCPM 排序或二价扣费。
