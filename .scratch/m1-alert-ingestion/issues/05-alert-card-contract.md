Status: resolved
Blocked by: 03, 04

# 05 事件卡片 JSON 契约 + 查询路由（G3 定案）

## 任务

事件卡片组装与查询路由（无 UI 阶段的验收载体，落 api 层）：

- `GET /alerts/{id}/context` → 事件卡片 JSON：**告警本体（归一化字段）+ 近期指标 + 服务拓扑 + 近期变更（可为空）**
- 可另加 `GET /alerts` 列表（分页/按指纹查），不做 UI

## 要点

- 术语纪律：这是**事件卡片 / Alert Card**（CONTEXT.md 已登记），不是「事件 / Incident」——M2 判定真实后才建档，措辞与字段命名不得混用
- 契约字段定型后是 M8 UI 与 M3 取证的输入，命名一次到位（评审过的 JSON 键名不再改）
- 上下文缺失源返回显式 `unavailable` 标记，不留 null 歧义

## 验收（可机械判定，实测后回填）

- [x] 对 03 产出的合并告警，GET 返回字段齐全的卡片 JSON（本体 + 三源，缺失源有标记）
  实测（2026-09-07）：oncall.db（issue 04 回放的末 8 条 webhook 产物）告警 #1
  （DemoCacheMissSpike）/ #2（DemoApiGwHighLatency）均为 dedup_count=2 的合并告警；
  `curl GET /alerts/2/context` → 200，本体 13 键齐全（含 am_fingerprint/raw_alert/webhook
  溯源），三源 metrics(topology) ok：metrics 3 series / topology 2 targets / changes 空
  占位；单测 `test_merged_alert_exposes_dedup_count` 守卫合并告警路径。
- [x] 卡片键名与设计文档/本 spec 契约一致（测试断言固定键名）
  契约键名已登记 `decisions.md` **D-17**：顶层 `{alert, context, generated_at}`；
  alert 本体 13 键；`context` = D-16 统一形状 `{"sources": [×3]}`；
  上下文时间锚 = `last_fired_at`（卡片描述「告警发生时」的状态，回放历史 dump 有意义）。
  单测以键集合**精确匹配**断言（多键少键都红）：
  `test_card_top_level_and_source_keys_are_fixed` / `test_alert_body_matches_persisted_row`。
- [x] 不存在的 id → 404；全量 pytest 绿
  实测：`GET /alerts/999/context` → 404（curl 实测 + 单测）；全量 pytest 绿
  （coverage 96.70% ≥80%）；ruff check / ruff format --check 绿；
  C6 守卫：card.py 111 行 / routes.py 46 行 / app.py 78 行，全部 ≤300。

## 实现备注（2026-09-07）

- 落点 `src/oncall/api/`：`card.py`（组装：`build_alert_card` / `alert_body` /
  `list_alerts`）+ `routes.py`（薄路由工厂，引擎与上下文客户端注入）；路由只做
  Session/404 薄封装，组装逻辑全在 card.py。
- `GET /alerts` 列表一并落地：`{items, total, limit, offset}`，limit 钳制
  [1, 100]，支持 fingerprint 过滤；列表只给本体不给上下文（上下文只在单卡接口，
  防列表请求逐行打 Prometheus）。
- `create_app` 新增 `context_config` / `context_client` 注入口（默认
  `ContextConfig.from_env()` / 生产客户端懒建）——测试替身从工厂进来，
  路由测试不触网。
- 联调实录：uvicorn(8010) + curl 真实卡片 JSON 三源齐全；`docker compose stop
  prometheus` 后 metrics/topology 均 `unavailable` + meta.reason（WinError 10061
  连接拒绝），changes 仍 ok，接口 200 不 5xx；恢复容器后三源回 ok。
- 悬空引用修复：issue 04 提交信息与实现备注声称「口径登记 D-16」，但
  decisions.md 实际漏写该行（全仓 grep 只有 config.py docstring 引用）——本次
  补记 D-16 并在状态列注明补记缘由。

## Blocked by

03, 04
