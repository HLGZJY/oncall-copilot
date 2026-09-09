"""架构守卫测试（AST 扫描）——ArchUnit 的 Python 对等物。

覆盖 quality-gates.md：C4(裸HTTP) / C5(LLM SDK散落) / C6(文件≤300行) /
C8(模块级可变全局) / A1(测试禁真实SDK) / A2(prompt内联拼接)。

报错信息一律按编码规范 §1 写成"原因 + 下一步怎么办"。
src/ 尚未创建时整组 skip，不空转。
"""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
SRC_ROOT = REPO_ROOT / "src" / "oncall"
TESTS_ROOT = REPO_ROOT / "tests"
MAX_FILE_LINES = 300  # C6: 单文件行数上限
MAX_INLINE_PROMPT = 200  # A2: 内联超长 prompt 判定阈值（字符）

HTTP_MODULES = {"httpx", "requests", "urllib3", "aiohttp"}
LLM_MODULES = {"openai", "anthropic"}
HTTP_ALLOWED = {"src/oncall/infra/http.py"}  # _rel_module 以仓库根为基准，含 src/ 前缀
LLM_ALLOWED = {
    "src/oncall/infra/llm.py",
    "src/oncall/infra/llm_planner.py",
    "src/oncall/infra/llm_judge.py",  # M7 issue 07：真实 judge client（同款收口）
}

# A1 的**实质**防线是 conftest 的 autouse 断网 fixture（pytest-socket 直接禁 socket），
# import 守卫只是第二道 lint。真实 LLM client 的「SDK 异常 → 契约异常」映射必须在单测
# 钉死（热切换前提：映射错了 D-07 的 0 漏报兜底就失效），而这需要 import SDK 的异常类型。
# 显式登记豁免文件（只 import 异常类型、不构造 client），新增文件必须在此登记并写明理由。
SDK_EXCEPTION_MAPPING_TESTS = {
    "tests/unit/test_infra_llm.py",
    "tests/unit/test_infra_llm_planner.py",  # 同款：Planner 异常映射单测，只 import openai 异常类型
    "tests/unit/test_infra_llm_judge.py",  # 同款：judge client 异常映射单测（M7 issue 07）
}
MUTABLE_FACTORIES = {"list", "dict", "set", "bytearray", "defaultdict", "Counter"}

# R6（G4）：ingest 主链路零 LLM 依赖——0 漏收不被外部 LLM 依赖拖垮。
# app.py 是组装点豁免（llm_channel 为可选注入，仅 /classify 触达）；
# 主链路四模块（归一化/指纹/落库/schema）连 oncall.classify 的 runtime import 都不允许。
INGEST_CHAIN_FILES = {
    "src/oncall/ingest/normalize.py",
    "src/oncall/ingest/fingerprint.py",
    "src/oncall/ingest/service.py",
    "src/oncall/ingest/schemas.py",
}


def _iter_py_files(root: Path) -> list[Path]:
    if not root.exists():
        return []
    return sorted(p for p in root.rglob("*.py") if "__pycache__" not in p.parts)


def _rel_module(path: Path) -> str:
    try:
        rel = path.relative_to(REPO_ROOT).as_posix()
    except ValueError:  # pragma: no cover
        rel = path.as_posix()
    return rel


@pytest.mark.skipif(not SRC_ROOT.exists(), reason="src/ 尚未创建，守卫自首个 src commit 生效")
class TestSourceGuards:
    def test_files_are_short(self):
        """C6: 单文件 ≤300 行。"""
        too_long: list[tuple[str, int]] = []
        for path in _iter_py_files(SRC_ROOT):
            lines = sum(1 for _ in path.open(encoding="utf-8"))
            if lines > MAX_FILE_LINES:
                too_long.append((_rel_module(path), lines))
        assert not too_long, (
            f"文件超过 300 行上限: {too_long}。"
            "下一步: 按'深模块=小接口+大实现'拆分；拆不动的先写 ADR 说明为什么。"
        )

    def test_no_bare_http_clients(self):
        """C4: HTTP 调用统一收口，仅 infra/http.py 可 import httpx/requests 等。"""
        offenders: list[str] = []
        for path in _iter_py_files(SRC_ROOT):
            if _rel_module(path) in HTTP_ALLOWED:
                continue
            tree = ast.parse(path.read_text(encoding="utf-8"))
            for node in ast.walk(tree):
                names = []
                if isinstance(node, ast.Import):
                    names = [a.name.split(".")[0] for a in node.names]
                elif isinstance(node, ast.ImportFrom) and node.module:
                    names = [node.module.split(".")[0]]
                bad = [n for n in names if n in HTTP_MODULES]
                if bad:
                    offenders.append(f"{_rel_module(path)} imports {bad}")
        assert not offenders, (
            f"发现裸 HTTP 客户端: {offenders}。"
            "下一步: 改经 oncall/infra/http.py 的统一客户端（超时/重试/脱敏在此统一）；"
            "确需新收口点先修改 docs/conventions/quality-gates.md。"
        )

    def test_no_scattered_llm_sdks(self):
        """C5: LLM SDK 只允许在 infra/llm.py，业务层走 LLMClient。"""
        offenders: list[str] = []
        for path in _iter_py_files(SRC_ROOT):
            if _rel_module(path) in LLM_ALLOWED:
                continue
            tree = ast.parse(path.read_text(encoding="utf-8"))
            for node in ast.walk(tree):
                names = []
                if isinstance(node, ast.Import):
                    names = [a.name.split(".")[0] for a in node.names]
                elif isinstance(node, ast.ImportFrom) and node.module:
                    names = [node.module.split(".")[0]]
                bad = [n for n in names if n in LLM_MODULES]
                if bad:
                    offenders.append(f"{_rel_module(path)} imports {bad}")
        assert not offenders, (
            f"LLM SDK 出现在收口点之外: {offenders}。"
            "下一步: 改走 oncall/infra/llm.LLMClient（成本埋点/结构化输出/模型分层在此统一）。"
        )

    def test_ingest_chain_has_zero_llm_dependencies(self):
        """R6: ingest 主链路零 LLM 依赖——禁 import oncall.classify 与 LLM SDK。"""
        offenders: list[str] = []
        for path in _iter_py_files(SRC_ROOT):
            if _rel_module(path) not in INGEST_CHAIN_FILES:
                continue
            tree = ast.parse(path.read_text(encoding="utf-8"))
            for node in ast.walk(tree):
                names = []
                if isinstance(node, ast.Import):
                    names = [a.name for a in node.names]
                elif isinstance(node, ast.ImportFrom) and node.module:
                    names = [node.module]
                for name in names:
                    if name.split(".")[0] in LLM_MODULES or name.startswith("oncall.classify"):
                        offenders.append(f"{_rel_module(path)} imports {name}")
        assert not offenders, (
            f"ingest 主链路出现 LLM 依赖: {offenders}。"
            "下一步: 分类逻辑只经 POST /classify 独立入口触达（R6/G4），"
            "主链路保持零 oncall.classify import；确需放宽先过 decisions.md 评审。"
        )

    def test_no_mutable_module_globals(self):
        """C8: 禁模块级可变全局；常量必须 UPPER_CASE。"""
        offenders: list[str] = []
        for path in _iter_py_files(SRC_ROOT):
            tree = ast.parse(path.read_text(encoding="utf-8"))
            for node in tree.body:  # 只看模块级
                if not isinstance(node, ast.Assign):
                    continue
                for target in node.targets:
                    name = getattr(target, "id", None)
                    if name is None or name.isupper():
                        continue
                    if name.startswith("__") and name.endswith("__"):
                        continue  # dunder 标准约定(__all__ 等)不受 C8 约束
                    value = node.value
                    mutable = isinstance(value, (ast.ListComp, ast.DictComp, ast.SetComp)) or (
                        isinstance(value, ast.Call)
                        and isinstance(value.func, ast.Name)
                        and value.func.id in MUTABLE_FACTORIES
                    )
                    if isinstance(value, (ast.List, ast.Dict, ast.Set)) or mutable:
                        offenders.append(f"{_rel_module(path)}::{name}")
        assert not offenders, (
            f"发现模块级可变全局(不污染全局): {offenders}。"
            "下一步: 改为 UPPER_CASE 常量 + Final 注解，或封装进类/函数作用域。"
        )

    def test_no_shell_true(self):
        """A6（M5 issue 05/G4/D-42）: 禁 shell=True 拼串——subprocess 一律列表参数。

        bandit B602 只覆盖部分调用形态，这里用 AST 扫描兜住全部 `shell=True`
        关键字实参（含变量别名绕不过：只放行字面量 True 的缺席）。
        """
        offenders: list[str] = []
        for path in _iter_py_files(SRC_ROOT):
            tree = ast.parse(path.read_text(encoding="utf-8"))
            for node in ast.walk(tree):
                if not isinstance(node, ast.Call):
                    continue
                for kw in node.keywords:
                    if (
                        kw.arg == "shell"
                        and isinstance(kw.value, ast.Constant)
                        and kw.value.value is True
                    ):
                        offenders.append(f"{_rel_module(path)}:行{node.lineno}")
        assert not offenders, (
            f"发现 shell=True 拼串调用: {offenders}。"
            "下一步: 改 subprocess 列表参数 + shell=False；命令一律经 "
            "remediation/allowlist.render_argv 白名单渲染（D-42），"
            "确需放宽先过 decisions.md 评审。"
        )

    def test_prompts_live_in_template_files(self):
        """A2: 业务代码禁止内联拼长 prompt；prompt 一律放 prompts/ 模板文件。"""
        offenders: list[str] = []
        for path in _iter_py_files(SRC_ROOT):
            tree = ast.parse(path.read_text(encoding="utf-8"))
            for node in ast.walk(tree):
                if isinstance(node, ast.Call) and getattr(node.func, "attr", None) in {
                    "create",
                    "chat",
                    "completions",
                }:
                    # 粗粒度启发：LLM 调用点必须来自 infra/llm（C5 已兜底），
                    # 这里抓的是关键字实参里出现超长字符串字面量（>200 字符的 system 提示）
                    for kw in node.keywords:
                        if (
                            isinstance(kw.value, ast.Constant)
                            and isinstance(kw.value.value, str)
                            and len(kw.value.value) > MAX_INLINE_PROMPT
                        ):
                            offenders.append(f"{_rel_module(path)}:行{node.lineno}")
        assert not offenders, (
            f"发现内联超长 prompt: {offenders}。"
            "下一步: 移入 prompts/ 模板文件并版本化，代码里只做变量填充。"
        )


class TestTestGuards:
    def test_unit_tests_do_not_import_real_sdks(self):
        """A1: 单元测试禁真实 SDK（openai/anthropic/httpx/requests）；走录制回放 fixture。"""
        if not TESTS_ROOT.exists():
            pytest.skip("tests/ 尚未创建")
        offenders: list[str] = []
        for path in _iter_py_files(TESTS_ROOT):
            if "integration" in path.parts:
                continue  # integration 单独 schedule，豁免
            if _rel_module(path) in SDK_EXCEPTION_MAPPING_TESTS:
                continue  # 只 import SDK 异常类型以钉死映射（见常量注释）
            tree = ast.parse(path.read_text(encoding="utf-8"))
            for node in ast.walk(tree):
                names = []
                if isinstance(node, ast.Import):
                    names = [a.name.split(".")[0] for a in node.names]
                elif isinstance(node, ast.ImportFrom) and node.module:
                    names = [node.module.split(".")[0]]
                bad = [n for n in names if n in LLM_MODULES | HTTP_MODULES]
                if bad:
                    offenders.append(f"{path.relative_to(REPO_ROOT)} imports {bad}")
        assert not offenders, (
            f"单元测试引入真实 SDK/网络客户端: {offenders}。"
            "下一步: 用录制回放 fixture（conftest 提供）；真调用请移入 tests/integration/ "
            "并加 pytest.mark.integration + token 预算。"
        )
