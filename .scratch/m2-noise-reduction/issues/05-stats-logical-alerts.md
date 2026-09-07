Status: ready-for-agent
Blocked by: 04

# 05 统计口径：逻辑告警归并 + 降噪率/漏报核算（T5 / D-20 定案）

## 任务

统计模块，落 `src/oncall/classify/stats.py`：

- **逻辑告警归并（D-20）**：canonical label 子集（`{alertname, job, instance}`，同 D-14 指纹输入不含桶）相同、相邻两行 `前.last_fired_at` 与 `后.fired_at` 间隔 ≤ `dedup_window` 的行，**传递**归并为 1 个逻辑告警；归并只发生在统计层，M1 落库行为不动
- **指标核算**：`R` = Σ `dedup_count`（D-15 口径）；`I` = 判为 incident 的逻辑告警数；**降噪率 = (R − I) / R**；漏报 = golden 标注 incident 的逻辑告警被判 false_positive 的数量（risk 不算漏报，单列）；输出指标字典（供 issue 07 报告与 M7 runner 复用，接口按「输入评测运行数据 → 输出指标字典」设计）

## 要点

- 跨桶边界归并是 D-14 的对偶：M1「超窗新行、保守不漏收」的落库语义保留，统计层把「同一故障连发跨桶开 2 行」还原为 1 个逻辑告警——否则「3 连发合并 1 条」类验收 flaky（M1 issue 06 实录已证该行为存在）
- 归并的传递性要测：3 行 A(fired 10:01)→B(fired 10:08)→C(fired 10:15) 在 window=10m 下归并为 1 个逻辑告警；间隔 >window 断链开新逻辑告警
- 分母 R 用 Σ `dedup_count` 而非行数——重复合并（M1 贡献）必须计入降噪分子，否则 80% 口径失真
- 本模块是纯函数 + 只读查询，M7 评测台将直接复用；golden 标注比对接口预留（issue 07 消费）

## 验收（可机械判定，实测后回填）

- [ ] 跨桶 2 行归并 1 逻辑告警单测绿（构造 fired_at 跨两个 10m 桶的同源连发）
- [ ] 传递归并与断链单测绿：3 行连发（间隔均 ≤window）→ 1 个；间隔 >window → 2 个
- [ ] 降噪率公式单测绿：构造数据（已知 R/I/FP/risk）核算值与期望一致；R=0 时口径不除零（返回约定值并在报告标注）
- [ ] 漏报核算单测绿：golden 标注 incident 被判 false_positive → 计 1；被判 risk → 不计漏报、单列
- [ ] 全量 pytest + ruff 绿

## Comments

-
