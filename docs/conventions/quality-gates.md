---
title: "约束层：工程规约与质量门禁"
summary: "广度调研结论 + 适配运维智能体的约束清单总表 + 每条约束的机械化落地方式"
source: Grinta-Coding-Agent rigour 门禁 + Agentic AI CI/CD 四道门禁 + Hamel Husain evals 主张 + Modern Python Tooling
status: active
updated: 2026-09-06
read_when: 新增约束、code-review 判断违规、或 CI 红了要查原因时
---

# 约束层：工程规约与质量门禁

## 1. 调研结论（五条核心判断）

1. **约束必须机械化，否则不存在。** Java 生态用 ArchUnit/Checkstyle/SpotBugs 做的事，Python 对应物是 **ruff（静态规则）+ import-linter（架构依赖）+ mypy（类型）+ pytest-cov（覆盖）+ bandit/pip-audit（安全与供应链）+ 自写 AST 守卫测试（工具没有的规则）**。参考项目 Grinta-Coding-Agent 用 rigour.yml 强制文件行数与 TODO 清零，证明"口头约定翻译成守卫测试"在 agent 项目里是成熟做法。
2. **Agent 项目比普通后端多一类约束面：非确定性部分。** 业界共识（Hamel Husain / buildpulse / ecorpit）：分层测试——①确定性外壳（模型 stub，普通单测）②轨迹不变量（断言"终态正确 + 路径性质"而非"精确步骤序列"）③评测回归门禁（黄金集分数回归超阈值阻断合并）。**CI 里确定性断言永远排在 LLM-as-judge 前面**——免费、瞬时、不自相矛盾。
3. **CI 必须断网。** 单元测试禁止真实 LLM API 调用（pytest-socket + 录制回放 fixture）；真调用的 integration 测试单独标记、单独 schedule、带 token 预算上限。
4. **工具选择上单工具化：ruff 一把梭。** 取代 black/isort/flake8/pylint 大部分；ruff **不支持自定义错误文案**（Checkstyle 的能力没有直接对等物），取舍是：需要"错误信息指导下一步"的规则全部写成 AST 守卫测试，断言失败消息按编码规范 §1 的"原因 + 下一步"格式写——正好把 Checkstyle 的诉求用更可控的方式满足。
5. **约束要有生效时机与豁免流程，否则约束层本身会腐化。** 本仓约束自首个 `src/` commit 起强制生效；豁免必须走"先改本规范再写码"，禁止 `# noqa` 无注释（对应"先改规范，不许代码悄悄偏离规范"）。

## 2. 约束清单总表

### 2.1 从 Java 参照清单适配的 9 条

| # | 口头约定 | 机械化规则 | 工具 / 载体 | 级别 |
|---|---|---|---|---|
| C1 | 锁定运行时与工具版本 | `requires-python = ">=3.11,<3.12"`；CI 锁 Python 3.11.x；pre-commit 全部锁 rev | `pyproject.toml` + `.github/workflows/ci.yml` | 阻断 |
| C2 | 所有依赖显式声明版本 | 依赖一律写区间约束 `>=x,<y`；lockfile 固定精确版本；CI `pip-audit` 扫 CVE（HIGH+ 阻断） | `pyproject.toml` | 阻断 |
| C3 | 分层依赖不可逆 | `harness/` 不得 import 任何业务模块；依赖只能向下 | import-linter contracts（pyproject 内） | 阻断 |
| C4 | HTTP 调用统一收口 | 仅 `oncall/infra/http.py` 可 import httpx/requests；其余模块禁裸 HTTP 客户端 | import-linter + AST 守卫 | 阻断 |
| C5 | LLM SDK 统一收口 | 仅 `oncall/infra/llm.py` 可 import openai/anthropic；业务层只能走 `LLMClient` | import-linter + AST 守卫 | 阻断 |
| C6 | 方法要短 / 文件要短 | 函数 ≤50 语句（ruff `PLR0915`）、参数 ≤5（`PLR0913`）、分支 ≤12（`PLR0912`）；**文件 ≤300 行**（ruff 无此规则 → AST 守卫） | ruff + `tests/test_architecture_guards.py` | 阻断 |
| C7 | 日志要规范 | 禁 `print`/`pprint`/`breakpoint`（ruff `T20`/`T10`）；日志必须走 structlog 且带上下文字段（review 检查） | ruff | 阻断 |
| C8 | 不污染全局 | 禁模块级可变全局（列表/字典/集合字面量赋值）；常量必须 UPPER_CASE（SpotBugs MS_* 对等物，ruff 无 → AST 守卫） | AST 守卫 | 阻断 |
| C9 | 测试要充分 | 行覆盖率 `fail_under = 80`；`src/oncall/harness/**` 单独 ≥85%（核心中的核心） | pytest-cov | 阻断 |

### 2.2 Agent 项目特有的 6 条（Java 清单没有的部分）

| # | 约束 | 机械化规则 | 工具 / 载体 | 级别 |
|---|---|---|---|---|
| A1 | 单测 CI 断网 | 单元测试禁 import 真实 SDK（openai/anthropic/httpx/requests）；CI 用 pytest-socket 断网；`integration` 标记的测试默认不跑、单独 schedule、带 token 预算 | AST 守卫 + pytest markers | 阻断 |
| A2 | prompt 模板化且版本化 | prompt 只能住 `prompts/` 目录模板文件；业务代码内联拼接 `messages=` 超 3 行 → AST 守卫报错指向模板机制 | AST 守卫 | 阻断 |
| A3 | 确定性断言优先 | 每个评测场景先写可机械断言的性质（结构化输出 schema 通过 / 关键工具被调用 / 步数 ≤15 / 证据落库），LLM-as-judge 只兜语义 | `src/oncall/eval/` 约定 + review | 强约定 |
| A4 | 轨迹不变量而非轨迹快照 | 评测断言写"终态 + 路径性质"（如：确认门前不得出现写工具调用、`remediate_*` 不得出现两次），禁止断言精确步骤序列 | `eval/` runner 约定 | 强约定 |
| A5 | 评测回归门禁 | 每次 commit 跑 dev 黄金集；Top-1 命中率跌幅 >10% 或成本涨幅 >50% → 阻断合入 | `ci.yml` eval job | 阻断 |
| A6 | 安全红线进流水线 | 禁 `shell=True` 拼串、禁硬编码密钥（bandit + gitleaks）；处置类命令必须经 PermissionGate（review 人工 + A4 不变量兜底） | bandit / pre-commit | 阻断 |

## 3. 逐项落地方式（对应文件）

| 落地物 | 覆盖约束 | 说明 |
|---|---|---|
| `pyproject.toml` | C1 C2 C6(函数) C7 C9 C3 C4 C5 | ruff / mypy(strict) / pytest / coverage / import-linter 全部集中配置 |
| `tests/test_architecture_guards.py` | C6(文件) C4 C5 C8 A1 A2 | AST 扫描守卫，报错信息含"下一步怎么办"；`src/` 不存在时自动 skip |
| `.github/workflows/ci.yml` | C1 C9 A5 A6 + 全部 lint | 五个 job：lint → type → test(断网+覆盖率) → security → eval-regression(条件触发) |
| `.pre-commit-config.yaml` | C7 A6 | ruff check/format + bandit + gitleaks，rev 全锁定（C1） |
| 本文件 | A3 A4 + 豁免流程 | 强约定类无法完全机械化，落地为 runner 编写规则与 review 检查项 |

## 4. 生效时机与豁免

- **生效时机**：`src/oncall/` 首个 commit 起，CI 全绿才可合入；此前守卫测试自动 skip，不空转。
- **豁免流程**：需要绕过约束 → 先修改本文件（写明原因与期限）→ 再写码。禁止无注释 `# noqa`；`# noqa: <规则>` 必须附一行原因。
- **防腐化**：约束清单与本文件每两周随体检（improve-codebase-architecture）过一遍，删掉已失效的，不层层堆叠。
