---
title: "OnCall Copilot 编码规范"
summary: "Python 工程约定 + Agent 特有规范（工具/prompt/上下文/成本）+ 测试与安全红线，可直接执行"
source: Anthropic Writing Tools/Context Engineering + SWE-agent ACI + ~/.workbuddy/开发规范与工作流程.md 第4/5章
status: active
updated: 2026-09-06
read_when: 写任何实现代码前；code-review 的 Standards 轴基线
---

# OnCall Copilot 编码规范

> 优先级：仓库文档化标准（本文）> PEP 8 / 类型检查器。Standard 轴 review 以本文为基线。

## 1. 工程约定

- **语言**：Python 3.11+，全量类型注解；`py.typed`；公开函数 100% 注解，内部函数至少注解参数
- **异步**：FastAPI 路由与工具 I/O 一律 `async`；CPU 密集（异常检测）丢 `run_in_executor`，不阻塞事件循环
- **格式**：ruff（lint + format），配置进 `pyproject.toml`；禁止裸 `# noqa`，要写原因
- **配置**：Pydantic Settings，构造时校验（OpenHands 原则：组件不可变），`.env` 只存密钥；禁止 `os.environ` 散落各处
- **错误**：自定义异常层级 `CopilotError → ToolError / PlannerError / GuardrailError`；**错误信息必须包含"下一步怎么办"**（SWE-agent：错误要指导更好的工具使用，不是抛堆栈）
- **日志**：structlog 结构化 JSON 日志；每条带 `incident_id`/`step_no` 上下文字段；禁止 print
- **依赖**：新增依赖须在 PR 说明用途与替代方案；能用标准库就不用三方

## 2. 目录结构约定

```
src/oncall/
├── harness/        # Loop/Planner/Verifier/ContextManager/PermissionGate —— 无业务语义
├── tools/          # 每工具一文件，注册即用；只依赖 tools/base.py 的接口
├── ingest/         # M1 webhook/指纹/归一化
├── classify/       # M2 规则通道 + LLM 通道
├── evidence/       # M4 存储与报告导出
├── remediation/    # M5 SOP 解析/闸门/验证
├── knowledge/      # M6 报告/向量化/召回
├── eval/           # M7 runner/judge/矩阵
├── api/            # FastAPI 路由（薄层，只做参数校验+调用服务）
└── models/         # Pydantic 模型 + SQLAlchemy 表
scenarios/dev/  scenarios/holdout/   # 黄金集，与代码同仓版本化
```

**依赖方向铁律**：`harness/` 不得 import 任何业务模块（它通过 `ToolRegistry` 接口工作）——这是可迁移性的物理保证。

## 3. Agent Harness 代码规范

### 3.1 循环与状态

- 单循环单线程；禁止在循环内起子 Agent / 线程池跑规划（可调试性优先，Claude Code 教训）
- **状态单一真值**：所有可变状态收敛在 `InvestigationSession`；工具与 Planner 无状态（构造时冻结配置）
- `Session.record()` 是唯一落库入口；禁止旁路写库（可回溯性的保证）
- 每步结束必须持久化后再进下一步——进程崩了能从最近 step 恢复

### 3.2 LLM 调用

- 结构化输出：Pydantic schema + `response_format`；解析失败重试 1 次（附错误说明让模型自修），再失败归类 `plan_error` 终止
- 每次调用记录：`model / prompt_tokens / completion_tokens / cost / latency`，写入 evidence step
- 模型分层：分类/摘要用便宜模型，规划用推理模型；通过 `LLMClient` 抽象切换（OpenAI-compatible）
- prompt 一律放 `prompts/` 目录模板文件，版本化；禁止字符串拼接埋在代码里；模板变量显式命名
- 禁止在 prompt 里写安全约束（"不要删库"无效），安全只在 PermissionGate 代码层

### 3.3 上下文预算（硬编码，可配置但默认生效）

| 项 | 预算 |
|---|---|
| 单次工具输出进上下文 | ≤ 2000 tokens（超出截断+落库+指针） |
| 系统提示 | ≤ 1500 tokens |
| 单步总上下文 | ≤ 8000 tokens，超限触发摘要化 |
| MAX_STEPS | 15 |

## 4. 工具开发规范（对标 Anthropic《Writing Tools for Agents》+ SWE-agent ACI）

每个新工具过这张 checklist，任一不满足不合入：

- [ ] **单一职责**：名字即行为（`query_metrics` 不顺带查日志）；相关操作合并为一个工具而非五个近亲（少而精 > 多而重叠）
- [ ] **命名空间前缀**：`metric_*` / `log_*` / `topo_*` / `kb_*` / `sop_*` / `remediate_*`，同类前缀防模型混淆
- [ ] **描述即文档**：docstring 写清何时用/何时不用/参数含义/返回结构/边界情况——按"新人入职文档"标准写
- [ ] **高信号输出**：返回人类可读名（服务名非 UUID）、带时间窗、按相关度排序；支持 `verbosity` 参数控制详略
- [ ] **反馈紧凑**：默认返回摘要 + 统计；原始数据落库
- [ ] **错误可操作**：异常信息含原因 + 建议下一步（如 `timeout: Prometheus 不可达; 建议: 检查 http://... 或调用 topo_get_health`）
- [ ] **确定性优先**：能用 PromQL 精确过滤的绝不让模型从大结果里自己挑（硬过滤 > 模型筛选，Anthropic：省 token 且准）
- [ ] **护栏内置**：`remediate_*` 工具必须声明风险级别，写操作强制四道闸门参数（dry_run/confirm_token）
- [ ] **埋点**：调用次数/冗余调用率/错误率/token 消耗自动上报（评测台的原始数据）

## 5. 测试规范

- TDD 红绿循环，测试只住约定接缝；测试名即规格（`test_fingerprint_merges_same_alert_within_window`）
- **独立真相源**：期望值来自手算/固定 fixture，禁止用实现逻辑重算期望
- LLM 相关单测用录制回放（fixture 化的响应），CI 不打真实 API；每次录制带日期与模型版本
- 混沌/时序测试固定 seed、冻结时间
- 模型会删失败测试骗完成度（Anthropic 实测）——**测试文件只增不删**，删测试必须 PR 单独说明
- 评测回归：dev 集 × 当前主模型，每次 commit 跑；命中率跌幅 > 10% 阻断合入

## 6. 安全红线（违反即回滚，无例外）

1. 写操作必须过 `PermissionGate`；绕过门禁直接调注入/处置函数 = P0 违规
2. 第 5 级操作（drop/delete/truncate 生产对象）在代码层不存在，不只靠审批
3. 密钥只经环境变量注入；日志与证据落库前过脱敏函数（密码/token/内网 IP）
4. SQL 全参数化；shell 命令白名单校验后 `shlex.join`，禁 `shell=True` 拼串
5. 送外部 LLM 的 payload 走统一脱敏出口，禁止工具直接组 prompt

## 7. 提交与评审

中文 + type 前缀（格式见 AGENTS.md）；诊断类提交写明被证实的假设。
Code-review 双轴：Standards 轴以本文为基线（额外常查：Speculative Generality、Primitive Obsession——Agent 代码最易犯的两个），Spec 轴对 issue。

## 8. 新会话开工自查（30 秒）

读 AGENTS.md → 读本文相关节 → 确认接缝 → 明确模块 → 开写。**规范冲突时：先改规范再写码，不许代码悄悄偏离规范。**
