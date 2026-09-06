---
title: "设计决策清单"
summary: "已拍板的关键决策及理由；ADR 的三条判据与存放位置"
source: docs/reference/_sources/项目信息.md + docs/prd.md + 2026-09-05 基调会话
status: active
updated: 2026-09-07
read_when: 动手前想知道"这事儿定过没有"；或要新写 ADR 时
---

# 设计决策清单

> 快速索引层。满足 ADR 三判据（难以逆转 / 无上下文会意外 / 真实权衡）的，升级写 `design/adr/NNNN-slug.md`（惰性创建）；其余留在这张表。

| # | 决策 | 结论 | 核心理由 | 状态 |
|---|---|---|---|---|
| D-01 | 数据集：静态 vs 活环境 | **轻量自建活环境为主 + 静态集背书** | Agent 需要"注入→告警→取证→处置→验证"活闭环；静态集（SNAP/LogHub）用于模块验证与回应"数据自造"质疑 | 已定 |
| D-02 | Astronomy Shop 的角色 | **不进关键路径**，可选/上云做迁移演示 | 6–10GB 内存，本机跑不动；标准性由 OTel/Prometheus 栈本身提供 | 已定 |
| D-03 | Agent 自研 vs 改开源 | **自研轻量 ReAct**，只读不改 OpenDerisk/LangGraph | 学是第一目标；改造成本高于自研；附 LangGraph 对比版 | 已定 |
| D-04 | 可迁移性实现方式 | **接缝 + adapter**，双环境演示证明 | 通用性是接口约束出来的，不是做出来的；口径克制（"对任何 Prometheus 系可迁移"） | 已定 |
| D-05 | 目标水位 | **L3 受控自动执行** | 白鳝分级；L4+ 的自治超出学生项目可信范围 | 已定 |
| D-06 | 开发基调 | 文档驱动 agentic engineering；核心模块提交前能 3 句话讲清 | "和 AI 写代码学到东西"的最低保障线 | 已定 |
| D-07 | 告警分类兜底 | 不确定落"风险"，不丢弃 | 0 漏报的保证 | 已定 |
| D-08 | RAG 定位 | 召回历史事故用 RAG，查当前状态用工具调用 | RAG 是工具不是架构 | 已定 |
| D-09 | M8 展示层选型与定位 | **Vite + Vue3 + Element Plus + ECharts + SSE**；定位 = 验收硬口径的可视化载体（P0 六件信息件不可砍，可砍的是动效与 P1）；mock 开关先行页面结构 | Element Plus 运维后台成熟、ECharts 覆盖漏斗/热力/对比曲线；无展示页则降噪率/成本/确认门无直观出口；设计文档 `design/m8-showcase-design.md` | 已定 |
| D-10 | issue 追踪器选型 | **本地 `.scratch/` markdown，纳入版本控制**（非 GitHub Issues） | 仓库无远端、不依赖外部服务；issue 与代码同版本控制后，"实现了什么"和"为什么这么做"在同一条历史里，半年后回溯上下文不丢。沿用 mattpocock/skills 的本地 tracker 约定（`.scratch/<feature>/issues/<NN>-<slug>.md` + 5 个 triage 角色），日后切 GitHub Issues 只需改标签映射表，其余约定不变 | 已定 |
| D-11 | 文档分层与母本归位 | 根目录 = 总纲（AGENTS/README/CONTEXT）；`docs/` = 细节知识权威；`docs/reference/_sources/` = 原始母本（archived，只回溯不引用）；跨项目工程纪律母本外置到 `~/.workbuddy/` | 消除"双权威"：此前根目录资料汇编与 `docs/` 提炼版内容同源，改哪份不明确 | 已定 |
| D-12 | M0 目录布局与 `scenario.yaml` 契约 | demo 系统与混沌脚本**不进 `src/oncall`**（`demo/` + `chaos/scenarios/` + `datasets/golden/` 顶层目录）；`scenario.yaml` 九字段定稿（name/fault_type/category/inject/inject_method/cleanup/expected_alerts/expected_root_cause/expected_remediation）作为 M7 runner 输入契约冻结 | 被观测目标与 Agent 代码物理隔离，防 coverage/import-linter 门禁误伤；字段名被 M7 引用后改名牵连大，评审日（2026-09-06）拍板 | 已定 |
| D-13 | `alert_events` 增列与迁移方式 | **增四列**：`dedup_count` / `last_fired_at` / `resolved_at` / `annotations_json`；`annotations_json` 同时存原始 payload 与 AM 自带 fingerprint（全 labels FNV-1a，易变，仅交叉溯源不作主指纹）；SQLite `create_all`（dev），Alembic 延至切 MySQL | 字段被 M2+ 引用后难改名；主指纹自算（canonical 子集 sha256）而非用 AM 自带 fingerprint，因其含易变 label 会抖动 | 已定（2026-09-06 评审 G5；issue 01–03 落地） |
| D-14 | M1 主指纹是否含时间窗 | **含**：`fingerprint` = sha256(canonical label 子集 `{alertname, instance, job}` 存在者参与、字典序 + 0xFF 分隔 + **时间窗桶**)；合并查询取「当前桶 + 前一桶」两个候选指纹实现滑窗语义 | `alert_events.fingerprint` 带唯一约束（D-13 去重锚点）。若指纹只由 label 子集算出，同一告警任何时刻只能有一行，「超窗新行」与 `dedup_window` 均无从表达；并入时间窗桶后唯一约束 / 超窗新行 / 窗口可配三者同时成立。代价：桶边界附近（9:59 与 10:01）分属两行——保守方向，不漏收 | 已定（2026-09-07，issue 03 落地；与 CONTEXT.md「指纹 = 规则+实例+时间窗算出的稳定哈希」一致） |
| D-15 | 计数口径：重放 vs 新 firing | **只在新 firing（`fired_at` 晚于 `last_fired_at`）时 `dedup_count++`**；原样重放 / 旧投递只跳过不计数；resolved 通知不计数，但若其 `startsAt` 晚于已见 firing 则计为一次新 firing（漏收兜底） | D-13 幂等硬要求（AM 非 2xx 退避重试）与「0 漏收」必须同时成立：重放不重复计数，未见过的 firing 即使只收到 resolved 也要补计 | 已定（2026-09-07，issue 03 落地；实测见 issue 03「0 漏收边界」） |
| D-16 | 上下文三源统一形状与配置口径 | **单源统一 `{source, status: ok\|unavailable, items, meta}`**，三源顺序固定 metrics → topology → changes；任一源失败只落该源 `unavailable` 标记（meta.reason），`collect_context` 永不抛错；配置走 `ContextConfig`（窗口默认 30m，URL/窗口/超时 env 可覆盖，非法值回落默认） | 上下文缺失 ≠ 告警缺失（0 漏收对上下文侧的延伸），调用方（M3 取证 / M8 UI）拿到统一形状不必逐源特判；配置回落默认——配置坏了不许拖垮拉取 | 已定（2026-09-07，issue 04 落地；**补记**：issue 04 提交时声称登记 D-16 但实际漏写本行，issue 05 回填） |
| D-17 | 事件卡片 JSON 契约键名 | **顶层 `{alert, context, generated_at}`**；`alert` 本体 13 键：`id / fingerprint / source / status / labels / annotations / am_fingerprint / raw_alert / webhook / fired_at / last_fired_at / resolved_at / dedup_count`（annotations/am_fingerprint/raw_alert/webhook 为 D-13 `annotations_json` 的溯源展开）；`context` = `collect_context` 原样输出（D-16 形状）；**上下文时间锚 = 该告警的 `last_fired_at`**（非当前时间）；不存在 id → 404；Prometheus 不可达 → 对应源 `unavailable`，卡片仍 200 | 契约是 M8 UI 与 M3 取证的输入，键名一次到位不再改；时间锚决定「近期指标」的语义——卡片描述「告警发生时」的近期状态，回放历史 dump 时上下文才有意义；单测 `test_alert_card_api.py` 以键集合精确匹配守卫本契约 | 已定（2026-09-07，issue 05 落地） |

## 待定（进入对应里程碑前必须 grill 敲定）

- [x] ~~issue 追踪器选型（GitHub Issues vs `.scratch/`）~~ → **已定，见 D-10**（2026-09-06）
- [x] ~~`CONTEXT.md` 首批术语的权威定义~~ → **已完成**（30 个术语 / 6 类，2026-09-06）
- [ ] 评测判对错细则（语义匹配规则 + LLM-as-judge 抽检比例）——M7 前
- [ ] 第二迁移演示环境选型（Sock Shop / 另一套 compose）——M8 前
- [ ] issue 追踪器是否升级为 GitHub Issues——建远端仓库时再评估
