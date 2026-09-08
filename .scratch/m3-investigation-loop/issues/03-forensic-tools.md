Status: ready-for-agent
Blocked by: 02

# 03 取证工具实现（T3 / G2·G3 推荐）

## 任务

四个真实取证工具，落 `src/oncall/harness/tools/`：

- **query_metrics**（G2 ①）：入参 `{promql, start?, end?, step?}`，时间缺省锚 `incident.alert.last_fired_at` ± D-16 窗口 → Prometheus `/api/v1/query_range`（R3），matrix 结果 series 上限 20、每 series 截取后纳入 ≤2000 tokens 预算
- **search_logs**（G2 ②）：入参 `{selector(LogQL), start?, end?, limit≤100, direction?}` → Loki `/loki/api/v1/query_range`（R4：limit 默认 100、direction 默认 backward）
- **detect_anomaly**（G3）：入参 `{values[], timestamps[]}`；**纯统计 v1**（stdlib `statistics`：基线窗口 z-score + 分位数/IQR + 环比突变），返回 `{status, anomalies[], meta.method}`；零新依赖、IsolationForest 不引入（M7 前按需评审）
- **get_topology**（G2 ⑤）：复用 `oncall.context`（`collect_context` / `service_topology`，不在 C3 禁列），返回 D-16 统一形状

## 要点

- HTTP 一律经 `oncall.infra.http.Fetcher` 注入（C4 收口）；Loki 查询走同一接缝，不新开 HTTP 通道
- 四态降级：Prometheus/Loki 不可达 → `{status: "unavailable", meta.reason}`，不抛错（上下文缺失 ≠ 调查失败，D-16 纪律延伸）
- 空结果 → `{status: "empty"}`（与 error/unavailable 区分，Planner 据此换向）
- LogQL/PromQL 选择器合法性在工具层做最小校验（括号/花括号配平），完整语义校验交给上游 400 降级为 error
- C6 单文件 ≤300 行：四工具分文件落位

## 验收（可机械判定）

- [ ] 每工具四态（ok/empty/error/unavailable）单测绿（注入 Fetcher 替身，断网单测全绿）
- [ ] query_metrics 时间锚缺省 `last_fired_at` ± 窗口单测绿；series 上限 20 截断
- [ ] search_logs limit 默认/上限 100、direction 默认 backward 单测绿
- [ ] detect_anomaly 统计判定单测绿（构造序列：正常/尖峰/水平漂移/空序列四类夹具）
- [ ] get_topology 复用 context 三源、D-16 形状单测绿
- [ ] 全量 pytest + ruff 双检绿
