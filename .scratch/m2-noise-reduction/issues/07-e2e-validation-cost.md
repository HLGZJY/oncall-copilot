Status: ready-for-agent
Blocked by: 04, 05, 06

# 07 端到端 3 剧本验证 + 成本实测回填（T7 收尾）

## 任务

真实栈端到端验收，M2 收尾：

- **3 剧本验证（PRD §7-M2）**：真实注入 `slow-sql`（基础设施）+ `protocol-mismatch`（业务语义层）+ `false-positive-flap`（历史误报）各 1 轮 → ingest → `POST /classify` → 统计报告；全部逻辑告警 verdict 与 golden `classification` 标注一致（含误报被识别），0 漏报
- **LLM 真实调用（需用户确认 key）**：接 DeepSeek-chat 与 Qwen-plus 真实 client（01/03 的接缝），实测单次分类成本与延迟，按 `cost = in_tokens × 单价 + out_tokens × 单价` 回填（单价取官方定价页快照，标注取价日期）
- **回填与收尾**：设计文档验收节逐条实测回填 + status 翻 `implemented`；本票 Comments 写实录

## 要点

- **真实调用前必须向用户确认 LLM API key 已备**（2026-09-07 拍板：设计期全 mock，实测在此票）——确认前本票的 LLM 部分不得执行
- 演练须记录：每剧本告警时间线 / dedup_count / 逻辑告警归并结果 / 每行 verdict 与 channel（rule 还是 llm）/ llm_calls 计数——规则先行可证（规则可判定行 0 次 LLM 调用）
- 若注入受容器栈状态影响，先 `docker-compose ps`（独立命令，非 compose 插件）确认 9 容器 Up；本机代理拦 127.0.0.1 → curl 加 `--noproxy '*'`；起服务 `PYTHONPATH=src`
- 实测数据禁虚构；某项未达标如实记录失败模式，不粉饰

## 验收（可机械判定，实测后回填）

- [ ] 3 剧本全逻辑告警 verdict 与 golden 一致（含 false-positive-flap 全部误报被归档），0 漏报
- [ ] 降噪率实测值回填设计文档验收节（D-20 口径核算）
- [ ] 规则先行计数可证：规则命中行 llm_calls 贡献为 0
- [ ] LLM 单次分类成本实测回填 ≤ ¥0.05 上限（G8）；两模型各至少 1 次真实调用记录
- [ ] 设计文档 status 翻 `implemented`；全量 pytest + ruff 绿
- [ ] spec.md 与本票状态收尾，M2 全部票 resolved

## Comments

-
