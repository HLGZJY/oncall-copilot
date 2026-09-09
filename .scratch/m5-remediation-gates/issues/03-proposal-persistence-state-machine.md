Status: resolved
Blocked by: 02（已解除）

# 03 proposal 落库与状态机（T3 / G8 / D-46）

## 任务

- **新增第八表 `remediation_proposals`**（超架构 §4 七表规划的显式偏差，**已评审定案 D-46**；随本票回写架构 §4 七表 → 八表）——字段照设计文档数据模型节：
  `id, incident_id(FK→incidents), investigation_id(FK→investigations, nullable——产出调查), runbook_slug, action_id, status, dry_run_json(干跑命令清单+影响面——**批准即锁定**，confirm/reject 用此渲染), params_json, decision, confirm_reason, confirmed_at, executed_at, verify_result_json, rollback_status, created_at, finished_at`
- SQLAlchemy `Base` 复用 + `create_all` 幂等（D-13/D-30 先例）；dev 单库延续，Alembic 延至 MySQL
- `src/oncall/remediation/service.py`（行数预算 ≈260）处置状态机：`pending→approved/rejected→executing→recovered/failed/rolled_back/escalated`（D-40/D-46 状态集）；非法迁移拒绝
- proposal 生命周期跨调查（pending 等确认发生在调查收尾后）——service 是 remediation 模块确定性核心，**无 LLM**

## 要点

- 状态机状态集与迁移表以测试钉死（非法迁移抛错）
- `dry_run_json` 是批准对象（D-39）——状态机任何路径不改写它
- incident 关联：proposal 锚 incident（一对多，一次事故可多次处置尝试）；investigation_id 可空（产出调查）
- `incidents.status` 翻 `mitigated` 是**既有枚举值流转**（架构 §4 枚举含 mitigated）——不扩列不扩枚举（D-19）
- 架构 §4 回写（七表 → 八表）随本票提交（评审后动作第 5 条预告）
- models.py 现 ≈200 行 + 第八表 ≈45 → ≈245 < C6 300，暂不拆子模块；若超限拆 `models_remediation.py`（同包无新边）

## 验收（可机械判定）

- [x] pytest 绿：`remediation_proposals` 建表 + 幂等 create_all；字段与设计文档数据模型节一致
- [x] pytest 绿：状态机合法迁移通过、非法迁移（如 pending→recovered 直跳、终态再迁移）拒绝并报错
- [x] pytest 绿：proposal 行字段全落（dry_run_json 原文、params_json、时间戳）；incident_id 关联正确
- [x] pytest 绿：investigation_id 可空路径（无产出调查的处置）可建行
- [x] pytest 绿：`dry_run_json` 全路径不可变断言（状态机不改写批准对象）
- [x] 架构守卫全绿（C3 remediation→db 合法、C6 models.py ≤300）
- [x] 全量门禁不回退（基线 580/10，票面旧值 541/10 已修正）+ ruff 双检

## 落位注记（实现后回填）

- **表最终列**（16 列，照 D-46 逐字段）：`id, incident_id(FK NOT NULL, index 不唯一——一对多多次处置尝试), investigation_id(FK nullable), runbook_slug, action_id, status(String(16) default pending, CHECK ck_remediation_proposals_status 八值), dry_run_json(JSON NOT NULL), params_json(JSON nullable), decision(String(16) nullable, CHECK ck_remediation_proposals_decision 仅 approve/reject 或 NULL——票面"可选 CHECK"采纳), confirm_reason(Text), confirmed_at, executed_at, finished_at, verify_result_json, rollback_status, created_at(default now(UTC))`。models.py 202→258 行 < C6 300，未拆 models_remediation.py。`db/__init__.py` 未扩导出（测试直取 `oncall.db.models.RemediationProposal`）。
- **状态机迁移表定稿**（`remediation/service.py::_TRANSITIONS`，模块常量 Final）：`pending → approved|rejected`；`approved → executing`；`executing → recovered|failed|rolled_back|escalated`（escalated 直边服务 D-45「回滚为空的 runbook 未恢复」路径）；五终态（rejected/recovered/failed/rolled_back/escalated）出边为空。时间戳口径：confirmed_at 落 approve/reject、executed_at 落 executing、finished_at 落任一终态。非法迁移抛 `ProposalStateError`（新增领域错误，未入 CONTEXT——语义即词条"状态流转"既有描述，无新共享概念）。
- **approved 态语义处理**：approved 保留为持久合法态（decision/confirm_reason/confirmed_at 落点）；04 同步链 pending→approved→executing→终态一调用内遍历，05/06 分段驱动同表复用；`start_execution` 只接 approved（pending 直跳执行被拒——四道闸门第二道不可绕过）。
- **接缝工厂**：`make_proposal_creator(session, incident_id, *, investigation_id=None) -> Callable[[dict], str]`，payload 3 键落 pending 行返回 str id，鸭子类型满足 execute.ProposalCreator（remediation 零 import harness，C3 零边）。
- **架构 §4 回写**：六张核心表 → 八张核心表（顺手修 M4 遗留计数），第八表行已增补（含 2026-09-09 G8/D-46 注记）。
- **必要适配**：`tests/unit/test_db_evidence_models.py` 两处表清单断言 5 表 → 6 表（第八表纳入 metadata 的机械更新，M4 行为断言零改动）；harness 行为测试零改动。
- **架构 §4 回写 commit**：（见本票 Closes 的 feature commit）
