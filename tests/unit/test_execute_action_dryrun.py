"""M5 issue 02 execute_action 干跑 handler 测试（T2 / G1 / D-39，裁决① dry-run 语义）。

干跑 handler 职责（D-39：推理与执行分离）：
- 解析 `action` = `runbook_slug/action_id` 定位器 → 经注入的 runbook 库接缝定位
  Runbook → 在 `.actions` 按 `.id` 命中动作 → 渲染「将执行的命令清单 + 影响面」
  （干跑预览）→ 经注入的 proposal 存根接缝产 proposal_id → 返回
  `ToolResult{ok, data:{dry_run_preview, proposal_id}}`
- 本票 handler **永不执行任何 demo 侧写操作**：零 subprocess、零容器操作、零 HTTP
  ——干跑调用后 demo 侧状态零变化（无执行器可注入，结构上保证）
- 参数模板 `$var`（issue 01 语义）在干跑期显式标注「运行时解析、执行期注入」，
  不静默吞掉（裁决②）
- 错误路径返回 `ToolResult{status: error, meta:{reason}}`，不抛原始异常（D-16）
- 六工具集合 + `ExecuteActionInput{action, params}` 形状冻结（D-23）不破

C3：harness 静态面不 import remediation——handler 只经注入的库加载接缝拿
Runbook 数据，类型仅本地结构 Protocol，import-linter 零新增 harness→remediation 边。
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from oncall.harness.tools.execute import build_execute_action_handler
from oncall.harness.tools.registry import TOOL_NAMES, ToolHandler, ToolResult, ToolStatus
from oncall.harness.tools.schemas import ExecuteActionInput
from oncall.remediation.runbook import load_runbook_library  # 组装点测试可 import（C3 豁免）

REPO_ROOT = Path(__file__).resolve().parents[2]
RUNBOOKS_DIR = REPO_ROOT / "remediation" / "runbooks"


def _library() -> dict[str, Any]:
    """组装点：预加载 runbook 库（remediation/runbooks 整库，2 份）供 loader 接缝包装。"""
    return load_runbook_library(RUNBOOKS_DIR)


def make_loader() -> Any:
    """把 runbook 库包成 (slug)->Runbook|None 加载接缝（测试替身注入 handler）。"""
    return _library().get


def make_handler(
    loader: Any | None = None,
    proposal_stub: Any | None = None,
) -> ToolHandler:
    """构造注入了接缝的干跑 handler；不传存根 → 注入 None（保留装配缺失路径可测）。"""
    return build_execute_action_handler(
        runbook_loader=loader if loader is not None else make_loader(),
        proposal_creator=proposal_stub,
    )


def _default_stub() -> Any:
    return lambda payload: "pending-stub-0001"


def invoke(action: str, params: dict[str, Any] | None = None, **handler_kw: Any) -> ToolResult:
    args = ExecuteActionInput(action=action, params=params or {})
    handler_kw.setdefault("proposal_stub", _default_stub())
    return make_handler(**handler_kw)(args, timeout_seconds=30.0)


def _step_previews(result: ToolResult) -> list[dict[str, Any]]:
    assert result.data is not None
    return result.data["dry_run_preview"]["commands"]  # type: ignore[index]


class TestDryRunHappyPath:
    def test_ok_returns_preview_and_proposal_id_with_unchanged_toolresult_shape(self) -> None:
        """注入执行器接缝后：返回 ok + 命令清单 + 影响面 + proposal_id，ToolResult 形状不变。"""
        result = invoke("cpu-spike/stop-stress-and-restore-cpuset")
        assert result.tool == "execute_action"
        assert result.status is ToolStatus.OK
        assert set(result.data or {}) == {"dry_run_preview", "proposal_id"}  # type: ignore[arg-type]
        assert result.data is not None
        preview = result.data["dry_run_preview"]  # type: ignore[index]
        assert preview["runbook_slug"] == "cpu-spike"
        assert preview["action_id"] == "stop-stress-and-restore-cpuset"
        assert isinstance(preview["commands"], list) and len(preview["commands"]) == 3
        # 影响面标注 demo 侧副作用
        assert "demo" in str(preview["impact"]).lower() or "容器" in str(preview["impact"])
        assert result.data["proposal_id"].startswith("pending-")  # type: ignore[index]

    def test_commands_render_atomic_op_plus_resolved_params(self) -> None:
        """清单以「原子操作 → 参数」形态呈现（D-42：命令字符串不来自 runbook 正文/模型）。"""
        result = invoke("cpu-spike/stop-stress-and-restore-cpuset")
        cmds = _step_previews(result)
        rendered = [c["command"] for c in cmds]
        assert "docker.remove_container name=cpu-spike-probe" in rendered
        assert "docker.remove_container name=pumba-cpu-spike" in rendered
        # issue 07 裁决：cpu-spike 第 3 步补 $cpuset_cores 运行时模板（与 issue 05
        # 白名单 {container, cores} 契约对齐），干跑期显式呈现不吞
        expected = "docker.restore_cpuset container=oncall-demo-api-gw-1 cores=$cpuset_cores"
        assert expected in rendered
        # 命令来自白名单原子操作引用 + runbook 参数模板，不是 runbook 正文/模型自由文本
        assert not any("rm -f" in c for c in rendered)
        assert all(c["action"].startswith(("docker.", "mysql.")) for c in cmds)

    def test_commands_have_impact_annotation(self) -> None:
        """每步命令带 demo 侧副作用标注（影响面）。"""
        for cmd in _step_previews(invoke("cpu-spike/stop-stress-and-restore-cpuset")):
            assert isinstance(cmd["impact"], str) and cmd["impact"]

    def test_runtime_param_explicitly_flagged_not_swallowed(self) -> None:
        """`$var` 模板参数（slow-sql session_id）在干跑期标注「运行时解析、执行期注入」。"""
        result = invoke("slow-sql/kill-lock-session")
        cmds = _step_previews(result)
        kill = next(c for c in cmds if c["action"] == "mysql.kill_session")
        assert "$session_id" in kill["command"] or "runtime" in kill["command"].lower()
        assert kill.get("runtime_params") == ["session_id"]  # 显式标注，不静默吞掉


class TestProposalSeam:
    def test_proposal_creator_receives_dryrun_payload_once(self) -> None:
        """存根接缝收到 dry_run_json 载荷并产 proposal_id（issue 03 落库实现填充此接缝）。"""
        calls: list[dict[str, Any]] = []

        def proposal_stub(payload: dict[str, Any]) -> str:
            calls.append(payload)
            return "pending-abcd"

        result = invoke("cpu-spike/stop-stress-and-restore-cpuset", proposal_stub=proposal_stub)
        assert result.status is ToolStatus.OK
        assert result.data is not None
        assert result.data["proposal_id"] == "pending-abcd"  # type: ignore[index]
        assert len(calls) == 1
        payload = calls[0]
        assert payload["runbook_slug"] == "cpu-spike"
        assert payload["action_id"] == "stop-stress-and-restore-cpuset"
        assert "dry_run_json" in payload
        assert payload["dry_run_json"]["runbook_slug"] == "cpu-spike"


class TestZeroDemoSideEffect:
    def test_dry_run_never_invokes_any_write_or_subprocess(self) -> None:
        """干跑后 demo 侧状态零变化：handler 不触任何写路径——结构上无执行器，替身记录即证明。"""
        touched: list[str] = []

        def proposal_stub(payload: dict[str, Any]) -> str:  # 只读存根：记录但不做任何写
            touched.append("proposal-stub")
            return "pending-xyz"

        result = invoke("slow-sql/kill-lock-session", proposal_stub=proposal_stub)
        assert result.status is ToolStatus.OK
        # 唯一被触碰的外部协作 = proposal 只读存根（建提案留痕，非 demo 侧写操作）；
        # 没有任何 docker/mysql/容器执行外呼路径（handler 不 import subprocess，无执行器可注入）
        assert touched == ["proposal-stub"]
        preview = result.data["dry_run_preview"]  # type: ignore[index]
        assert preview["action_id"] == "kill-lock-session"
        assert all(c["action"].startswith(("docker.", "mysql.")) for c in preview["commands"])


class TestErrorPaths:
    def test_malformed_action_missing_slash(self) -> None:
        result = invoke("no-slash-here")
        assert result.status is ToolStatus.ERROR
        assert result.data is None
        assert "action" in str(result.meta["reason"])
        assert "runbook_slug" in str(result.meta["reason"]) or "/" in str(result.meta["reason"])

    def test_runbook_slug_not_in_library(self) -> None:
        result = invoke("nonexistent-runbook/some-action")
        assert result.status is ToolStatus.ERROR
        assert result.data is None
        assert "nonexistent-runbook" in str(result.meta["reason"])

    def test_action_id_not_found(self) -> None:
        result = invoke("cpu-spike/no-such-action")
        assert result.status is ToolStatus.ERROR
        assert result.data is None
        assert "no-such-action" in str(result.meta["reason"])

    def test_error_does_not_raise_raw_exception(self) -> None:
        # D-16：handler 错误一律折进 meta.reason，不向上抛原始异常
        result = invoke("cpu-spike/definitely-missing")
        assert isinstance(result.meta["reason"], str)


class TestUninjectedKeepsStubSemantics:
    def test_no_proposal_seam_injected_returns_error(self) -> None:
        """未注入 proposal 存根 → 干跑 handler 装配缺失返回 error（D-16，不抛原始异常）。"""
        handler = build_execute_action_handler(runbook_loader=make_loader(), proposal_creator=None)
        result = handler(
            ExecuteActionInput(action="cpu-spike/stop-stress-and-restore-cpuset"),
            timeout_seconds=30.0,
        )
        assert result.status is ToolStatus.ERROR
        assert result.data is None
        assert "装配" in str(result.meta["reason"])

    def test_no_runbook_loader_injected_returns_error(self) -> None:
        handler = build_execute_action_handler(
            runbook_loader=None, proposal_creator=_default_stub()
        )
        result = handler(
            ExecuteActionInput(action="cpu-spike/stop-stress-and-restore-cpuset"),
            timeout_seconds=30.0,
        )
        assert result.status is ToolStatus.ERROR
        assert "装配" in str(result.meta["reason"])


class TestFrozenSurface:
    def test_six_tools_and_input_shape_unchanged(self) -> None:
        """D-23 冻结面：六工具集合与 ExecuteActionInput 键集合不因实装而变。"""
        assert set(TOOL_NAMES) == {
            "query_metrics",
            "search_logs",
            "detect_anomaly",
            "get_topology",
            "query_kb",
            "execute_action",
        }
        assert set(ExecuteActionInput.model_fields) == {"action", "params"}
        assert ExecuteActionInput.model_config.get("extra") == "forbid"
