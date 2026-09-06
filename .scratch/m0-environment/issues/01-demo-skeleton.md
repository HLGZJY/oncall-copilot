Status: resolved
Blocked by: （无，frontier 首个）

# 01 demo 业务系统骨架

## 任务

搭建被观测 demo 系统：api-gw（FastAPI）接请求 → worker（Celery）消费任务 → MySQL/Redis，docker-compose 一键拉起。

## 要点

- 目录：`demo/api_gw/`、`demo/worker/`、`demo/common/`（Python 包名不能用连字符，故与设计文档措辞略有出入），根目录 `docker-compose.yml`；**代码不进 `src/oncall`**（D-12）
- api-gw 暴露：`GET /health`（200 + `{status, db, redis, queue_depth}`）、`POST /tasks`（提交异步任务）、`GET /metrics`（本 issue 可先空挂，T2 填充）
- compose healthcheck 接 `/health`；MySQL 业务表最小化（一张任务表即可）
- ruff 覆盖 `demo/`（同步改 `pyproject.toml` ruff `src`/include），mypy 豁免；coverage 维持只算 `src/oncall`

## 验收（机械判定）

- [x] `docker compose up -d` 全部服务 healthy（api-gw/mysql/redis healthy，worker running）
- [x] `curl /health` 返回 200 且 db/redis/queue_depth 字段齐备（实测 `{"status":"ok","db":"up","redis":"up","queue_depth":0}`）
- [x] `POST /tasks` 提交的任务被 worker 消费（task 1: pending → done，worker 日志 `Task demo.process_task[...] succeeded in 0.25s`）
- [x] `ruff check demo/` 通过（连带修复 ruff 0.16 上浮暴露的存量问题：RUF001-003 与中文注释冲突、tests 魔法数、docs md 代码块误入 format）

## Comments

### 2026-09-06 实现（Agent）

- 落地文件：`demo/common/{settings,db}.py`、`demo/api_gw/main.py`、`demo/worker/main.py`、`demo/Dockerfile.{api-gw,worker}`、`demo/requirements.txt`、根 `docker-compose.yml`（name: oncall-demo）
- 设计决策偏差已回写：包名 `api-gw` → `api_gw`（Python 标识符限制），设计文档与 issue 同步修正
- pyproject 同步：ruff `src` += demo、`exclude` += docs（md 内嵌代码块）；mypy `exclude` += demo/chaos（D-12）
- 附加修复：RUF001/002/003 全局豁免（中文全角标点是仓库语言约定）；`tests/test_architecture_guards.py` 魔法数 300/200 提为 `MAX_FILE_LINES`/`MAX_INLINE_PROMPT` 常量
- worker 保留 0.2s 处理延时，供 T2 延迟/队列深度指标观测
- 环境：Docker Desktop 需手动/后台启动后 compose 才可用；mysql:8.0 首次拉取约 7 分钟
