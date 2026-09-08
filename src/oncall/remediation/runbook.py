"""scenarios：runbook 文档库 —— 处置流程 markdown 的 schema 与解析器。

职责：把 `remediation/runbooks/*.md` 处置 runbook 解析为可被 execute_action 引用
的 `Runbook` 数据类；契约校验失败即拒绝加载（脏 runbook 进不了执行面）。

契约（D-43 / D-42 / D-44 / D-45，评审定案）：
- frontmatter（`---` 定界）YAML 六字段：slug / alert_ref / severity / actions[] /
  rollback[] / verification；正文 = 处置说明（进模型上下文，非执行源）
- actions[i] = {id, name, steps:[白名单原子操作引用 + 参数模板]}；steps 引用
  D-42 静态原子操作表内动作（命名空间.操作 两级），动作名不在注册表 → 拒绝
- rollback = 反向原子操作序列；显式空 `[]` = 无回滚预案（D-45），合法
- verification = {promql, condition, window_s}（D-44：恢复判据显式声明）
- 本模块零 LLM / 零 HTTP / 零 subprocess；仅做「切分 + 结构/引用校验 + 数据类」，
  不含处置编排逻辑（编排归 service，白名单正则归 issue 05）

本票裁决（偏离 D-43 字面，issue Comments 已留痕）：
① frontmatter 本体复用既有 `pyyaml`（pyproject 已含 M0 起 `pyyaml>=6.0,<7`，
  `scenarios/schema.py` 先例）——C2「零新依赖」指不新增包；自写 YAML 子集解析器
  是易错技术债。只 frontmatter 定界切分自写（见 `_split_frontmatter`）。
② 动作族注册表 = 模块级不可变 frozenset（C8 合规），扁平存 `ns.op` 全名，
  issue 05 allowlist 以此为动作名一致性的单一权威扩展参数模板。
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any, Final

import yaml
from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator, model_validator

SLUG_PATTERN = re.compile(r"^[a-z0-9][a-z0-9-]*$")

# 裁决②：D-42 静态原子操作表的最小动作名注册表（与 chaos cleanup.sh 处置语义对齐）。
# 存 `命名空间.操作` 两级全名；C8 要求不可变 → frozenset 常量。
ACTION_KEYS: Final[frozenset[str]] = frozenset(
    {
        "docker.remove_container",  # docker rm -f <name>（停探针/Pumba 等辅助容器）
        "docker.restore_cpuset",  # docker update --cpuset-cpus=... <api-gw>（恢复原始 cpuset）
        "mysql.kill_session",  # mysql KILL <session_id>（KILL 持锁会话释放表锁）
    }
)

Scalar = str | int | float | bool


class RunbookValidationError(Exception):
    """runbook 校验失败（含来源与具体字段），脏 runbook 拒绝加载的载体。"""


# ── Pydantic 数据类 ────────────────────────────────────────────────────────


class RunbookStep(BaseModel):
    """一个白名单原子操作引用 + 参数模板（结构层，正则匹配归 issue 05）。"""

    model_config = ConfigDict(extra="forbid")

    action: str = Field(min_length=1, description="命名空间.操作 两级引用，须在 ACTION_KEYS 内")
    params: dict[str, Scalar] = Field(
        default_factory=dict,
        description="参数模板；`$var` 前缀 = 运行时解析变量，本票只做结构校验",
    )


class RunbookAction(BaseModel):
    """runbook 的一个处置动作（steps 全部通过白名单引用原子操作）。"""

    model_config = ConfigDict(extra="forbid")

    id: str = Field(min_length=1, pattern=r"^[a-z0-9][a-z0-9-]*$")
    name: str = Field(min_length=1)
    steps: list[RunbookStep] = Field(min_length=1)

    @model_validator(mode="after")
    def _steps_all_reference_registered_actions(self) -> RunbookAction:
        unknown = [s.action for s in self.steps if s.action not in ACTION_KEYS]
        if unknown:
            msg = f"action {self.id!r} 引用了白名单外原子操作 {sorted(set(unknown))}"
            raise ValueError(msg)
        return self


class RunbookVerification(BaseModel):
    """runbook 显式声明的恢复判据（D-44）——恢复验证机械断言的对象。"""

    model_config = ConfigDict(extra="forbid")

    promql: str = Field(min_length=1)
    condition: str = Field(min_length=1, description="判据的自然语言/比较式，如 p95 <= 0.05")
    window_s: int = Field(gt=0, description="观察窗（秒），判据需持续回落的窗口")


class Runbook(BaseModel):
    """一份处置 runbook（Markdown frontmatter 六字段 + 正文处置说明）。"""

    model_config = ConfigDict(extra="forbid")

    slug: str = Field(pattern=SLUG_PATTERN.pattern)
    alert_ref: str = Field(min_length=1, description="对齐 golden alert_timeline[].alert_name")
    severity: str = Field(min_length=1)
    actions: list[RunbookAction] = Field(min_length=1)
    rollback: list[RunbookStep] = Field(
        default_factory=list,
        description="反向原子操作序列；显式空 [] = 无回滚预案（D-45），合法",
    )
    verification: RunbookVerification
    body: str = Field(default="", description="正文处置说明（进模型上下文，非执行源）")

    @field_validator("rollback")
    @classmethod
    def _rollback_only_registered_actions(cls, v: list[RunbookStep]) -> list[RunbookStep]:
        unknown = [s.action for s in v if s.action not in ACTION_KEYS]
        if unknown:
            msg = f"rollback 引用了白名单外原子操作 {sorted(set(unknown))}"
            raise ValueError(msg)
        return v


# ── frontmatter 切分（自写定界器，YAML 本体复用既有 pyyaml）─────────────


def _split_frontmatter(text: str) -> tuple[str, str]:
    """把 markdown 切成 (frontmatter_yaml, body)；无合法定界则抛 RunbookValidationError。

    定界规则：文件以 `---` 独占一行开头，下一个 `---` 独占行结束 frontmatter；
    两定界之间的内容即 frontmatter YAML。找不到起始/结束定界即视为无 frontmatter。
    """
    if not text.startswith("---"):
        msg = "runbook 必须以 '---' frontmatter 定界开头（无 YAML frontmatter）"
        raise RunbookValidationError(msg)
    lines = text.splitlines()
    # lines[0] 已是起始定界 `---`；找下一个独占行定界
    end = None
    for i in range(1, len(lines)):
        if lines[i].strip() == "---":
            end = i
            break
    if end is None:
        msg = "runbook frontmatter 缺结束定界 '---'（frontmatter 未闭合）"
        raise RunbookValidationError(msg)
    frontmatter = "\n".join(lines[1:end])
    body = "\n".join(lines[end + 1 :])
    return frontmatter, body


# ── 解析与校验 ─────────────────────────────────────────────────────────────


def _frontmatter_to_dict(text: str) -> dict[str, Any]:
    frontmatter, body = _split_frontmatter(text)
    try:
        data = yaml.safe_load(frontmatter) or {}
    except yaml.YAMLError as exc:  # pragma: no cover - 低概率路径，仍兜底
        msg = f"frontmatter YAML 解析失败：{exc}"
        raise RunbookValidationError(msg) from exc
    if not isinstance(data, dict):
        msg = f"frontmatter 顶层必须是映射（dict），实际是 {type(data).__name__}"
        raise RunbookValidationError(msg)
    return data, body


def parse_runbook(text: str) -> Runbook:
    """解析一段 runbook markdown 文本；契约校验失败抛 RunbookValidationError。"""
    data, body = _frontmatter_to_dict(text)
    data["body"] = body
    try:
        return Runbook.model_validate(data)
    except ValidationError as exc:
        # 精简为单行原因链，报「哪个字段 + 为什么」
        reasons = "; ".join(
            f"{'.'.join(str(p) for p in e['loc'])}:{e['msg']}" for e in exc.errors()
        )
        msg = f"runbook 契约校验失败（拒绝加载）：{reasons}"
        raise RunbookValidationError(msg) from exc


def load_runbook_file(path: str | Path) -> Runbook:
    """读取并校验单个 runbook 源文件；失败抛 RunbookValidationError（含文件路径）。"""
    p = Path(path)
    try:
        return parse_runbook(p.read_text(encoding="utf-8"))
    except RunbookValidationError as exc:
        raise RunbookValidationError(f"{p}: {exc}") from exc


def load_runbook_library(runbooks_dir: str | Path) -> dict[str, Runbook]:
    """扫描目录内全部 *.md runbook，全部通过才产出 {slug: Runbook} 可用库。

    任一文件失败 → 整体不可用（fail-closed，脏 runbook 进不了执行面），
    抛 RunbookValidationError 并列失败文件与原因。
    """
    d = Path(runbooks_dir)
    if not d.is_dir():
        msg = f"runbooks 目录不存在：{d}"
        raise RunbookValidationError(msg)
    files = sorted(p for p in d.glob("*.md") if p.is_file())
    library: dict[str, Runbook] = {}
    failures: list[str] = []
    for f in files:
        try:
            rb = load_runbook_file(f)
        except RunbookValidationError as exc:
            failures.append(str(exc))
            continue
        library[rb.slug] = rb
    if failures:
        msg = "runbook 库加载失败（整体不可用），失败文件：\n  " + "\n  ".join(failures)
        raise RunbookValidationError(msg)
    return library
