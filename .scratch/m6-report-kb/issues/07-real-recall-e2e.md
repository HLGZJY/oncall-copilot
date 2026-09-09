Status: resolved
Blocked by: 06

# real recall e2e

## 任务

真实召回 e2e：本地 embedding 模型（bge-small-zh-v1.5）+ 活 demo 栈同故障注入两遍——第二遍报告含 kb 引用（首步即引用，可录屏）；召回得分/耗时如实回填（禁虚构）

## 验收（可机械判定）

- [x] 实测数据回填验收节与 issue Comments；设计文档翻 implemented 收口（2026-09-09，见下方 Comments 实测注记；`docs/design/m6-report-kb-design.md` 已翻 `implemented`）

## Comments（T7 执行期注记）

### 2026-09-09 开工环境核查与模型首载

- **本地模型检索**：全机无 bge-small-zh-v1.5 缓存（`~/.cache/huggingface` 不存在、`AppData` 无 hub 目录、项目内无模型目录）→ 按指示自行下载。
- **首载踩坑两条（如实记录）**：
  1. huggingface_hub 直连被墙（xet CAS 通道 401）→ 切 `HF_ENDPOINT=hf-mirror.com` + `HF_HUB_DISABLE_XET=1`；
  2. hub 下载通道经本机代理/镜像组合**持续返回 0 字节文件**（requests 同 URL 正常）→ 放弃 hub，用 requests 按 API siblings 清单逐文件手工落盘到 `C:\Users\heguo\.cache\oncall-models\bge-small-zh-v1.5`（model.safetensors 95.8MB，实测 16.5s）。
- 验证：加载 11.0s（首载 CPU）、512 维、encode 75ms；`ONCALL_BGE_MODEL_DIR` 可覆盖模型目录。

### 2026-09-09 真实面划分（照 M4-08 / M5-T8 先例，禁虚构）

| 面 | 真实度 | 说明 |
|---|---|---|
| 告警链路 | 真实 | chaos 02-slow-sql 注入活栈 → Prom 规则 → AM → compose oncall /ingest（共享 sqlite，容器零改动） |
| 分类面 | 真实 | POST /classify 规则先行 + 真实 LLM（qwen3.7-flash）→ incidents 1:1 建档 |
| 调查面 | 真实 | 真实 OpenAIPlannerClient + 真实取证工具（Prom 9090 / Loki 3100）+ 开局召回前置；Verifier 裁决接缝留 mock（default=证实，M4-08 先例，真判官归 M7） |
| 处置面 | 执行/验证真实，干跑触发确定性 | execute_action 干跑经真实 handler 由驱动脚本确定性触发（M4-08 实测真实 Planner 从不自发 execute_action）；confirm approve → 真实 ControlledExecutor（docker/mysql CLI）→ 真实 RunbookRecoveryVerifier（观察窗轮询代理，M5 先例） |
| KB 面 | 真实 | 本地 bge-small-zh-v1.5 真实嵌入 + InMemoryVectorStore（权威 kb_chunks 表） |

### 2026-09-09 实测回填（slow-sql 活栈两遍，实测明细 `.scratch/tmp/m6-07-real-recall-e2e.json`）

| 轮次 | 事件 | 告警等待 | 调查 | 确认链 | KB 入库 |
|---|---|---|---|---|---|
| 第一遍 | incident 11（alert 59） | 85.5s | escalated / plan_error（真实 Planner 2 步未收束，如实记录）/ 2.9s | recovered，106.7s | 5 块（五节齐全；自动触发失败走 `/kb/ingest` 回退，见下方注记） |
| 第二遍 | incident 12（alert 64） | 85.5s（去重窗等待 580.4s 后注入） | **缓存复用出口：`reused_from=11`，0.2s，不重查不建新调查** | 不适用（复用出口） | 不适用（复用不重复入库） |

- **PRD 硬口径达成**：复现同一故障第二次，开局召回首步即引用历史案例（指纹精确命中 → D-57 缓存复用，`no_reinvestigation=true`）。
- **向量召回得分/耗时（retriever.search 实测，禁虚构）**：query=`DemoDbPoolSaturated@mysql tasks 表写锁 连接池打满`，Top-3 得分 **0.6815 / 0.6345 / 0.6277**（opening_card / remediation / timeline，全部命中源事件 11），单次召回 **0.151s**（bge CPU）。
- **成本**：调查 + 分类真实 LLM 调用均为 qwen3.7-flash 短调用（单轮 planner 3 次 ≈1.9k tokens），远低于 ¥0.5 上限。

### 2026-09-09 顺带抓出并修复的两个生产缺陷（T7 e2e 的价值）

1. **T4 装配顺序缺陷（P1）**：`create_app` 中调查路由注册先于 kb wiring——路由工厂闭包按引用捕获 deps 对象，其后 `dataclasses.replace` 生成的 `kb_retriever` 路由侧永远看不到，**开局召回/复用出口在 create_app 接线面下失明**（T4 测试直注 `kb_retriever` 故未暴露）。修复：kb wiring 提前至路由注册之前；回归测试 `test_kb_pipeline_wiring_via_create_app_enables_reuse`（红→绿）。
2. **调查 500 观测盲点（P2）**：`/investigate` 非预期异常被 API 层吞成 500 不落日志，e2e 期故障不可诊断。修复：`logger.exception` 落堆栈（仍不向客户端泄漏）。
3. **遗留（未修，登记风险）**：confirm 长事务内 D-56 自动入库偶发失败（best-effort 语义吞异常，疑似 sqlite 写锁竞争），本次 e2e 走 `/kb/ingest` 合法回退面完成入库。建议后续票把 D-56 触发点挪出 confirm 长事务（处置链收尾后异步触发）。

### 2026-09-09 门禁收口

- 全量 pytest：**711 passed / 13 skipped**（基线 710/12 → +1 回归测试，只增不减）；ruff 双检全绿。
- 设计文档 `docs/design/m6-report-kb-design.md` 翻 `implemented`，验收节实测回填；D-49–D-57 落位核对无误。
