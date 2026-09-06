# OnCall Copilot — M0 一键复现入口（issue 06 / T6）
# 设计：docs/design/m0-environment-design.md；目录契约：docs/design/decisions.md D-12
#
# COMPOSE 自动探测：优先 `docker compose` 插件，缺失时回退 standalone `docker-compose`
# （本机为 standalone docker-compose v5.5.0，无 compose 插件——两种都能用）。

COMPOSE := $(shell docker compose version >/dev/null 2>&1 && echo "docker compose" || echo "docker-compose")
PY ?= python
API_URL ?= http://127.0.0.1:8000

.PHONY: help up down demo-load inject cleanup collect-run test

help:
	@echo "make up                                    # 拉起全栈（demo + Prometheus/Loki/Grafana + 告警链路）"
	@echo "make down                                  # 停止全栈（保留数据卷）"
	@echo "make demo-load [DURATION_SEC=60]           # 向 POST /tasks 打温和业务负载"
	@echo "make inject SCENARIO=01-cpu-spike          # 注入故障剧本（也接受 slug：cpu-spike）"
	@echo "make cleanup SCENARIO=01-cpu-spike         # 清理剧本注入（参数口径同 inject）"
	@echo "make collect-run SCENARIO=04-downstream-timeout RUN=r1"
	@echo "                                           # 黄金集单轮采集（issue 05 工具，注入→告警→清理→落档）"
	@echo "make test                                  # ruff check + format 校验 + 全量 pytest"

up:
	$(COMPOSE) up -d --build
	@echo "==> 已拉起。等待健康检查就绪后：curl $(API_URL)/health 应返回 {\"status\":\"ok\",...}"

down:
	$(COMPOSE) down

# 温和持续负载：keep-alive POST /tasks 制造真实业务流量（演示/预热用，
# 默认强度不触发告警；探针容器化——Windows 宿主机直接循环 curl 不可靠）
demo-load:
	DURATION_SEC=$(or $(DURATION_SEC),60) bash chaos/tools/demo-load.sh

# SCENARIO 既接受目录名（01-cpu-spike）也接受 slug（cpu-spike）
define resolve_scenario
SDIR="chaos/scenarios/$(1)"; \
if [ ! -d "$$SDIR" ]; then \
  SDIR=$$(grep -rl "^name: $(1)$$" chaos/scenarios/*/scenario.yaml | head -1 | xargs dirname); \
fi; \
[ -d "$$SDIR" ] || { echo "剧本不存在: $(1)（可用目录名或 slug，见 chaos/scenarios/）"; exit 1; }
endef

inject:
	@test -n "$(SCENARIO)" || { echo "用法: make inject SCENARIO=01-cpu-spike"; exit 1; }
	@$(call resolve_scenario,$(SCENARIO)); \
	echo "==> inject: $$SDIR"; bash "$$SDIR/inject.sh"

cleanup:
	@test -n "$(SCENARIO)" || { echo "用法: make cleanup SCENARIO=01-cpu-spike"; exit 1; }
	@$(call resolve_scenario,$(SCENARIO)); \
	echo "==> cleanup: $$SDIR"; bash "$$SDIR/cleanup.sh"

# 包装 datasets/golden/tools/collect_run.sh（issue 05 采集工具）；
# slug 从目录名剥掉序号前缀（04-downstream-timeout → downstream-timeout）
collect-run:
	@test -n "$(SCENARIO)" && test -n "$(RUN)" || { echo "用法: make collect-run SCENARIO=04-downstream-timeout RUN=r1"; exit 1; }
	@$(call resolve_scenario,$(SCENARIO)); \
	SLUG=$$(basename "$$SDIR" | sed 's/^[0-9]*-//'); \
	echo "==> collect-run: $$SDIR ($$SLUG) run=$(RUN)"; \
	bash datasets/golden/tools/collect_run.sh "$$SDIR" "$$SLUG" "$(RUN)"

test:
	$(PY) -m ruff check .
	$(PY) -m ruff format --check .
	$(PY) -m pytest tests
