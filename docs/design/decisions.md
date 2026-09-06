---
title: "设计决策清单"
summary: "已拍板的关键决策及理由；ADR 的三条判据与存放位置"
source: docs/reference/_sources/项目信息.md + docs/prd.md + 2026-09-05 基调会话
status: active
updated: 2026-09-06
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

## 待定（进入对应里程碑前必须 grill 敲定）

- [x] ~~issue 追踪器选型（GitHub Issues vs `.scratch/`）~~ → **已定，见 D-10**（2026-09-06）
- [x] ~~`CONTEXT.md` 首批术语的权威定义~~ → **已完成**（30 个术语 / 6 类，2026-09-06）
- [ ] 评测判对错细则（语义匹配规则 + LLM-as-judge 抽检比例）——M7 前
- [ ] 第二迁移演示环境选型（Sock Shop / 另一套 compose）——M8 前
- [ ] issue 追踪器是否升级为 GitHub Issues——建远端仓库时再评估
