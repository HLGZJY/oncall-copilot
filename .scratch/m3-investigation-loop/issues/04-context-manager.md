Status: ready-for-agent
Blocked by: 01

# 04 ContextManager（T4 / 架构 §3.3 定值）

## 任务

上下文预算管理，落 `src/oncall/harness/context_manager.py`：

- **摘要模板固定**（架构 §3.3）：组件/指标/异常方向/时间窗四要素——工具输出摘要按模板生成，压缩 KV cache 重复前缀
- **工具输出预算**：单次 ≤2000 tokens 进上下文，超出截断 + `[truncated, full at step N]` 指针（M3 指针 = EvidenceStep 对象引用，M4 建表后替换为行 id，G4）
- **系统提示预算**：≤1500 tokens——角色 + 工具一览（名称/一句话/何时用）+ 输出协议；工具详情 just-in-time（`tool_help` 由 Planner 按需查，不预载）
- **步数 ≥10 移出已证伪假设**主上下文（落 session 保留）

## 要点

- token 估算用 M2 同款粗估口径（G8：2 字符≈1 token，中英混排误差可接受；估算函数可注入替换）
- 摘要生成是纯函数（输入 EvidenceStep/ToolResult → 输出摘要串），TDD 接缝
- 模板文本落模块级常量（A2：禁业务代码内联 >200 字符 prompt 拼接，照 M2 `classify/llm/prompt.py` 先例）
- 依赖注入：session 与步数读取经接口，便于 T6 复用

## 验收（可机械判定）

- [ ] 摘要模板纯函数单测绿（四要素齐全、确定性输出、同输入同输出）
- [ ] 2000 tokens 截断边界单测绿（恰等于不截 / 超出截断 + 指针可回溯到 EvidenceStep 原文）
- [ ] 系统提示 ≤1500 tokens 断言绿（六工具一览 + 输出协议组装后仍在线内）
- [ ] 步数 ≥10 移出已证伪假设单测绿（窗口不再含 rejected 假设、session.hypotheses 仍保留、active/confirmed 不动）
- [ ] 全量 pytest + ruff 双检绿
