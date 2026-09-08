"""runbook 契约与解析器测试（TDD，M5 issue 01 / T1 / G5 / D-43）。

契约（D-43 / D-42 / D-44 / D-45，评审定案，实现票不得擅改）：
- Markdown frontmatter（`---` 定界）六字段：slug / alert_ref / severity / actions[] /
  rollback[] / verification；正文 = 处置说明（进模型上下文，非执行源）
- actions[i] = {id, name, steps:[白名单原子操作引用 + 参数模板]}
  step.action = `命名空间.操作` 两级（如 mysql.kill_session），只可引用动作族注册表
  内原子操作（D-42 静态原子操作表语义）；参数模板 `$var` 前缀 = 运行时解析变量，
  本票只做结构校验，正则匹配归执行器层（issue 05）
- rollback = 反向原子操作序列；显式 `[]` = 无回滚预案（D-45：cpu-spike 处置即恢复）
- verification = {promql, condition, window_s}（D-44：恢复判据显式声明）
- 解析落 src/oncall/remediation/runbook.py（C3 禁列已预置）；frontmatter 切分自写，
  复用既有 pyyaml 解析 YAML 本体（裁决①，C2 零新依赖字面满足）；契约校验失败即
  拒绝加载（脏 runbook 进不了执行面）
"""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml

from oncall.remediation.runbook import (
    ACTION_KEYS,
    Runbook,
    RunbookValidationError,
    load_runbook_file,
    load_runbook_library,
    parse_runbook,
)

REPO_ROOT = Path(__file__).resolve().parents[2]
RUNBOOKS_DIR = REPO_ROOT / "remediation" / "runbooks"


def dump(frontmatter: dict) -> str:
    """把一个 runbook frontmatter dict 包进 markdown 定界返回文本（测试夹具）。"""
    return "---\n" + yaml.safe_dump(frontmatter, sort_keys=False) + "---\n处置说明\n"


def _cpu_step() -> dict:
    return {"action": "docker.remove_container", "params": {"name": "cpu-spike-probe"}}


def _valid_action() -> dict:
    return {
        "id": "stop-stress-restore-cpuset",
        "name": "停探针/Pumba 并恢复 cpuset",
        "steps": [_cpu_step()],
    }


def _valid_frontmatter(**overrides: object) -> dict:
    fm: dict[str, object] = {
        "slug": "cpu-spike",
        "alert_ref": "DemoApiGwHighLatency",
        "severity": "warning",
        "actions": [_valid_action()],
        "rollback": [],
        "verification": {
            "promql": "histogram_quantile(0.95, demo_x_bucket)",
            "condition": "p95 <= 0.05",
            "window_s": 60,
        },
    }
    fm.update(overrides)
    return fm


# ── 1) frontmatter 切分 / 解析 ─────────────────────────────────────────────


def test_parse_runbook_returns_runbook_object() -> None:
    """合法 frontmatter 解析为一个 Runbook，slug 一致。"""
    runbook = parse_runbook(dump(_valid_frontmatter()))
    assert isinstance(runbook, Runbook)
    assert runbook.slug == "cpu-spike"


def test_frontmatter_missing_is_rejected() -> None:
    """无 frontmatter 定界（纯 markdown 正文）→ 拒绝加载。"""
    with pytest.raises(RunbookValidationError):
        parse_runbook("# 只有正文，没有 frontmatter\n")


def test_body_kept_separate_from_frontmatter() -> None:
    """frontmatter 与正文分离：正文不进数据字段，作为 Runbook.body 保留。"""
    text = (
        "---\n"
        + yaml.safe_dump(_valid_frontmatter(), sort_keys=False)
        + "---\n处置说明：停探针并恢复 cpuset。\n"
    )
    runbook = parse_runbook(text)
    assert "停探针" in runbook.body


# ── 2) 六字段全对 ─────────────────────────────────────────────────────────


def test_all_six_fields_populated() -> None:
    """六字段全对：slug/alert_ref/severity/actions/rollback/verification 落位。"""
    fm = _valid_frontmatter()
    runbook = parse_runbook(dump(fm))
    assert runbook.slug == "cpu-spike"
    assert runbook.alert_ref == "DemoApiGwHighLatency"
    assert runbook.severity == "warning"
    assert len(runbook.actions) == 1
    assert runbook.actions[0].id == "stop-stress-restore-cpuset"
    assert runbook.actions[0].name == "停探针/Pumba 并恢复 cpuset"
    assert runbook.actions[0].steps[0].action == "docker.remove_container"
    assert runbook.actions[0].steps[0].params == {"name": "cpu-spike-probe"}
    assert runbook.rollback == []
    assert runbook.verification.promql.startswith("histogram_quantile")
    assert runbook.verification.window_s == 60


# ── 3) 契约校验拒绝 ───────────────────────────────────────────────────────


@pytest.mark.parametrize("missing", ["slug", "alert_ref", "severity", "actions", "verification"])
def test_missing_required_field_rejected(missing: str) -> None:
    """字段缺省 → 拒绝加载并报清晰原因。"""
    fm = _valid_frontmatter()
    fm.pop(missing)
    with pytest.raises(RunbookValidationError) as exc:
        parse_runbook(dump(fm))
    assert missing in str(exc.value)


def test_empty_actions_rejected() -> None:
    """actions 为空列表 → 拒绝加载（无处置动作的 runbook 不可用）。"""
    fm = _valid_frontmatter(actions=[])
    with pytest.raises(RunbookValidationError) as exc:
        parse_runbook(dump(fm))
    assert "actions" in str(exc.value)


def test_verification_missing_promql_rejected() -> None:
    """verification 缺 promql → 拒绝加载（恢复判据不完整）。"""
    fm = _valid_frontmatter()
    fm["verification"] = {"condition": "p95 <= 0.05", "window_s": 60}
    with pytest.raises(RunbookValidationError) as exc:
        parse_runbook(dump(fm))
    assert "promql" in str(exc.value)


def test_verification_missing_condition_rejected() -> None:
    """verification 缺 condition → 拒绝加载。"""
    fm = _valid_frontmatter()
    fm["verification"] = {"promql": "histogram_quantile(0.95, demo_x_bucket)", "window_s": 60}
    with pytest.raises(RunbookValidationError) as exc:
        parse_runbook(dump(fm))
    assert "condition" in str(exc.value)


def test_unknown_atomic_action_rejected() -> None:
    """step 引用不存在于动作族注册表的原子操作 → 拒绝加载并点名。"""
    fm = _valid_frontmatter()
    fm["actions"] = [
        {
            "id": "bad",
            "name": "越权操作",
            "steps": [{"action": "docker.rm_any_container", "params": {"name": "x"}}],
        }
    ]
    with pytest.raises(RunbookValidationError) as exc:
        parse_runbook(dump(fm))
    assert "docker.rm_any_container" in str(exc.value)


def test_unknown_rollback_action_rejected() -> None:
    """rollback 引用不存在原子操作 → 拒绝加载。"""
    fm = _valid_frontmatter(
        rollback=[{"action": "mysql.drop_database", "params": {"container": "mysql"}}]
    )
    with pytest.raises(RunbookValidationError) as exc:
        parse_runbook(dump(fm))
    assert "mysql.drop_database" in str(exc.value)


def test_explicit_empty_rollback_allowed() -> None:
    """rollback 显式空 `[]` = 无回滚预案，合法（D-45：cpu-spike 处置即恢复）。"""
    runbook = parse_runbook(dump(_valid_frontmatter()))
    assert runbook.rollback == []


# ── 4) 动作族注册表 ───────────────────────────────────────────────────────


def test_action_keys_cover_cleanup_semantics() -> None:
    """注册表覆盖 cpu-spike + slow-sql 两组 cleanup.sh 处置动作语义（裁决②落位）。"""
    assert "docker.remove_container" in ACTION_KEYS
    assert "docker.restore_cpuset" in ACTION_KEYS
    assert "mysql.kill_session" in ACTION_KEYS


# ── 5) 库加载面 ───────────────────────────────────────────────────────────


def test_load_runbook_library_success(tmp_path: Path) -> None:
    """目录内全部文件合法 → 产出可用库 {slug: Runbook}。"""
    (tmp_path / "cpu-spike.md").write_text(dump(_valid_frontmatter()), encoding="utf-8")
    (tmp_path / "slow-sql.md").write_text(
        dump(_valid_frontmatter(slug="slow-sql")), encoding="utf-8"
    )
    lib = load_runbook_library(tmp_path)
    assert set(lib) == {"cpu-spike", "slow-sql"}


def test_load_runbook_library_any_failure_means_unusable(tmp_path: Path) -> None:
    """目录内任一文件失败 → 整体不可用（fail-closed）并列出失败文件+原因。"""
    (tmp_path / "ok.md").write_text(dump(_valid_frontmatter()), encoding="utf-8")
    (tmp_path / "broken.md").write_text(dump(_valid_frontmatter(actions=[])), encoding="utf-8")
    with pytest.raises(RunbookValidationError) as exc:
        load_runbook_library(tmp_path)
    assert "broken.md" in str(exc.value)


# ── 6) 2 个真实 runbook 源文件 + 语义对齐抽查（关键词级）────────────────


def test_real_cpu_spike_runbook_parses() -> None:
    """仓内 remediation/runbooks/cpu-spike.md 解析通过。"""
    rb = load_runbook_file(RUNBOOKS_DIR / "cpu-spike.md")
    assert rb.slug == "cpu-spike"
    assert rb.alert_ref == "DemoApiGwHighLatency"


def test_real_slow_sql_runbook_parses() -> None:
    """仓内 remediation/runbooks/slow-sql.md 解析通过。"""
    rb = load_runbook_file(RUNBOOKS_DIR / "slow-sql.md")
    assert rb.slug == "slow-sql"
    assert rb.alert_ref == "DemoDbPoolSaturated"


def test_cpu_spike_body_aligns_with_cleanup_and_golden() -> None:
    """cpu-spike 正文处置说明与 cleanup.sh + golden expected_remediation 语义对齐。"""
    rb = load_runbook_file(RUNBOOKS_DIR / "cpu-spike.md")
    # cleanup.sh 语义：停探针 + 停 Pumba/stress-ng + 恢复 cpuset
    assert "Pumba" in rb.body and "cpuset" in rb.body
    # golden remediation 判据：非 DB 路径 P95 回落 0.05s 以内
    assert "P95" in rb.body
    assert "0.05" in rb.verification.condition


def test_slow_sql_body_aligns_with_cleanup_and_golden() -> None:
    """slow-sql 正文处置说明与 cleanup.sh + golden expected_remediation 语义对齐。"""
    rb = load_runbook_file(RUNBOOKS_DIR / "slow-sql.md")
    # cleanup.sh 语义：KILL 持锁会话释放表锁
    assert "KILL" in rb.body and "表锁" in rb.body
    # golden remediation 判据：连接池占用回落 + 阻塞请求排空
    assert "连接池" in rb.body


def test_real_library_fully_loads() -> None:
    """remediation/runbooks/ 整库加载成功，恰好 2 个 runbook。"""
    lib = load_runbook_library(RUNBOOKS_DIR)
    assert set(lib) == {"cpu-spike", "slow-sql"}
