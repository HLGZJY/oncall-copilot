---
title: "M0 执行细案：环境与故障注入"
summary: "最小环境搭建步骤、8 类故障剧本、黄金集生成流程、验收标准"
source: docs/reference/_sources/数据集介绍.md 第三/五部分 + docs/prd.md §7（实现步骤 M0）
status: seed
updated: 2026-09-06
read_when: W1 执行 M0 时
---

# M0 执行细案

## 环境选型（决策 D-01/D-02）

- **主环境**：极简自研 docker-compose（FastAPI + Celery + MySQL/Redis），2–3GB 内存，日常迭代
- **不进关键路径**：OTel Astronomy Shop（本机吃力，留给云服务器做迁移演示）
- 备选参考：Sock Shop（级联故障/告警风暴，学术基准）

## 搭建步骤（约 1 周，每天 1–2h）

1. demo 业务系统：api-gw 接请求 → worker 消费任务 → MySQL/Redis；暴露 `/health` + 业务指标
2. 埋点：prometheus_client 输出 QPS/延迟/队列深度/DB 连接；日志落盘接 Loki
3. Prometheus 告警规则 5–8 条（对应剧本）；Grafana 看板 1 张（可选）
4. 故障注入脚本库 8+：bash/python 注入

## 8 类故障剧本清单

| 类别 | 故障 | 注入手段 |
|---|---|---|
| 资源类 | CPU 飚高 / OOM | Pumba |
| 网络类 | 下游超时 / 丢包 | Pumba |
| 业务类 | 慢 SQL / 死锁 | 自定义脚本 |
| 负载类 | 队列堆积 / 连接打满 | Locust/k6 |
| 故障类 | 进程被杀 / 缓存雪崩 | Pumba + 自定义脚本 |
| 语义层 | **版本不匹配协议不兼容**（加分项，必须有 1 个） | 自定义 |

## 黄金评测集生成（每个剧本 × 3 次）

记录：触发/恢复时间、触发的全部告警（数量/内容/时间）、**预标注根因 + 标准排查路径 + 标准处置** → 结构化标注文件（M3/M7 的评测基准）。

## 验收标准

一键拉起 → 人工触发 CPU 故障 → Grafana/PromQL 可见异常 → 收到告警。
数据质量自查：每个剧本至少 1 个指标可检异常？关键错误在日志里？拓扑可查询？
