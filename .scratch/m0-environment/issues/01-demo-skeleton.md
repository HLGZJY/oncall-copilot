Status: ready-for-agent

# 01 demo 业务系统骨架

## 任务

搭建被观测 demo 系统：api-gw（FastAPI）接请求 → worker（Celery）消费任务 → MySQL/Redis，docker-compose 一键拉起。

## 要点

- 目录：`demo/api-gw/`、`demo/worker/`、`demo/common/`，根目录 `docker-compose.yml`；**代码不进 `src/oncall`**（D-12）
- api-gw 暴露：`GET /health`（200 + `{status, db, redis, queue_depth}`）、`POST /tasks`（提交异步任务）、`GET /metrics`（本 issue 可先空挂，T2 填充）
- compose healthcheck 接 `/health`；MySQL 业务表最小化（一张任务表即可）
- ruff 覆盖 `demo/`（同步改 `pyproject.toml` ruff `src`/include），mypy 豁免；coverage 维持只算 `src/oncall`

## 验收（机械判定）

- [ ] `docker compose up -d` 全部服务 healthy
- [ ] `curl /health` 返回 200 且 db/redis/queue_depth 字段齐备
- [ ] `POST /tasks` 提交的任务被 worker 消费（任务表状态从 pending → done，可 curl 查询或查库验证）
- [ ] `ruff check demo/` 通过

## Blocked by

（无，frontier 首个）
