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
| D-17 | 事件卡片 JSON 契约键名 | **顶层 `{alert, context, generated_at}`**；`alert` 本体 13 键：`id / fingerprint / source / status / labels / annotations / am_fingerprint / raw_alert / webhook / fired_at / last_fired_at / resolved_at / dedup_count`（annotations/am_fingerprint/raw_alert/webhook 为 D-13 `annotations_json` 的溯源展开）；`context` = `collect_context` 原样输出（D-16 形状）；**上下文时间锚 = 该告警的 `last_fired_at`**（非当前时间）；不存在 id → 404；Prometheus 不可达 → 对应源 `unavailable`，卡片仍 200 | 契约是 M8 UI 与 M3 取证的输入，键名一次到位不再改；时间锚决定「近期指标」的语义——卡片描述「告警发生时」的近期状态，回放历史 dump 时上下文才有意义；单测 `test_alert_card_api.py` 以键集合精确匹配守卫本契约 | 已定（2026-09-07，issue 05 落地） || D-18 | 剧本 schema 十字段 + 黄金集三标注字段同源纪律 | **scenario.yaml 九字段（D-12）扩为十字段**：新增 `expected_investigation_path`（标准排查路径唯一权威源，第 1 步须指向 inject.sh 直接产生的首个可独立观测信号）；golden 的 `root_cause` / `investigation_path` / `remediation` 三字段必须与 `expected_*` **逐字一致**（dev/holdout 双集同步）；`golden_matches_scenario` 从"只比名字"升级为逐字段比对；`load_golden_tree(scenarios_dir=...)` 增 **R6**：timeline alertname ⊆ expected_alerts + 三字段一致性（不传 scenarios_dir 时仅树校验，向后兼容）；真实数据守卫在 `test_scenario_catalog.py::test_golden_tree_cross_validates_against_scenarios` | P2 教训：investigation_path 此前无权威源导致跨剧本复制无人拦截；P1 教训：预标注推演级联（DB 变慢连带队列堆积）实测 0 firing 却写入预期——判分基准标注错 = 评测全错，且结构缺口不修同类漂移会再次发生 | 已定（2026-09-07，M0-05 双盲复核校准落地；方案取舍见 docs/design/golden-set-calibration-design.md） |
| D-19 | M2 落库形态与三态出口 | **`alert_events` 增列 `classification_json`**（verdict/confidence/reason/channel/model/tokens/cost/classified_at 审计全量）；**status 枚举照架构 §4 `deduped→classified` 不扩**（三态存 verdict，status 只表达是否已分类）；risk 不由 LLM 直出，由置信度阈值（默认 0.7 可配）派生；LLM 输出契约 `{verdict: false_positive\|incident, confidence, reason}` + JSON mode + Pydantic 校验；真实事件 1:1 建 incidents 最小集（`id/alert_ids/severity/status/created_at`），跨告警聚合 Non-goal（M3 再议）；分类走独立 `POST /classify`，不串联 `/ingest`（ingest 主链路保持零 LLM） | 扩 status 枚举 = 推翻架构 §4 冻结模型，JSON 单列可审计可查询（SQLite JSON1）；规则只判误报/放行、不判真实，防规则误杀真事件；`/ingest` 0 漏收不被外部 LLM 依赖拖垮（webhook 重试语义）；incidents 五字段对齐六表规划，M3/M4 外键衔接 | 已定（2026-09-07，M2 评审 G3/G4/G5；依据 R2/R7，见 m2-denoise-classify-design.md §7） |
| D-20 | 降噪统计口径与逻辑告警 | **单位 = 逻辑告警（Logical Alert）**：canonical label 子集（同 D-14 指纹输入不含桶）相同、相邻两行 `前.last_fired_at` 与 `后.fired_at` 间隔 ≤ `dedup_window` 的行传递归并为 1 个逻辑告警——**只发生在统计层，M1 落库行为不动（D-14 不改）**；`R` = Σ `dedup_count`（D-15 口径）；**降噪率 = (R − I) / R**（I = 判为 incident 的逻辑告警数）；**漏报** = golden 标注 incident 的逻辑告警被判 false_positive 的数量，必须 = 0（risk 不算漏报，单列观察）；判真标准 = golden `classification` 标注 | D-14 桶边界使跨 10m 桶同源连发开 2 行（M1 实录已证），若按行统计则「3 连发合并 1 条」类验收 flaky；统计层归并保留 M1「超窗新行、保守不漏收」语义；漏报判定锚 golden 标注——标注错 = 评测全错（D-18 教训） | 已定（2026-09-07，M2 评审 G6） |
| D-21 | 误报样本来源与 golden 标注扩展 | **dev 集新增 1 个误报类剧本** `false-positive-flap`（注入脚本不注入业务故障，仅临时下调 Prometheus 告警规则阈值在正常水位误触发——配置漂移型误报）；golden `alert_timeline` 条目增**可选**字段 `classification`（`incident` 缺省 / `false_positive`），向后兼容，D-18 十字段与三字段逐字一致纪律不动；**3 验证剧本 = slow-sql + protocol-mismatch + false-positive-flap**；holdout 同步延至 M7 前，M2 期间 holdout 禁看不变 | PRD §7-M2 硬要求「3 剧本含 1 个历史上误报的告警被识别为误报」——现 golden 11 剧本全是真实故障，无误报样本；误报样本不构成就无法验证 0 漏报的另一半（不冤枉真告警的同时能识别假告警）；可选字段扩展不破坏既有 golden 校验（R6） | 已定（2026-09-07，M2 评审 G7；执行票 issue 06 标 `ready-for-human`，涉 D-12/D-18 契约面。**落地补记（issue 06）**：`classification` 以 `AlertEvent` 可选字段实现（缺省 incident，既有 11 剧本零改动回归绿）；R2 成对规则与 holdout 同步延期的冲突用**显式常量 `HOLDOUT_SYNC_PENDING` 过渡豁免**（清单内 slug 允许 dev 单边，R1/R3-dev/R6 照跑；不放宽 R2 为警告，M7 同步 holdout 后清空还原硬约束）；`ScenarioCategory` 扩第七类**「误报类」**（误报剧本六类无处安放）；误报走第 9 条专属规则 DemoTasksLatencyFlap 阈值下调（0.12s→0.01s）真实评估触发，golden 时间线为 dump 实测数据） |


## 待定（进入对应里程碑前必须 grill 敲定）

- [x] ~~issue 追踪器选型（GitHub Issues vs `.scratch/`）~~ → **已定，见 D-10**（2026-09-06）
- [x] ~~`CONTEXT.md` 首批术语的权威定义~~ → **已完成**（30 个术语 / 6 类，2026-09-06）
- [ ] 评测判对错细则（语义匹配规则 + LLM-as-judge 抽检比例）——M7 前
- [ ] holdout 同步（false-positive-flap 等新剧本补 holdout 采集至每剧本 ×3）并清空 `HOLDOUT_SYNC_PENDING`（src/oncall/scenarios/schema.py）恢复 R2 成对硬约束——M7 前（D-21 / issue 06）
- [ ] 第二迁移演示环境选型（Sock Shop / 另一套 compose）——M8 前
- [ ] issue 追踪器是否升级为 GitHub Issues——建远端仓库时再评估
