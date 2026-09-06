"""pytest 全局配置：src 导入路径 + 单测默认断网（A1，pytest-socket 可选）。"""

import sys
from pathlib import Path

SRC_ROOT = Path(__file__).resolve().parents[1] / "src"
if str(SRC_ROOT) not in sys.path:
    sys.path.insert(0, str(SRC_ROOT))

import pytest  # noqa: E402  (须在 sys.path 注入之后)


@pytest.fixture(autouse=True)
def _offline_by_default(request):
    """单元测试默认断网；带 integration 标记的测试例外（单独 schedule）。

    inproc_asgi 豁免：FastAPI TestClient 走进程内 ASGI 传输，不发真实网络请求，
    但事件循环自管道会被 pytest-socket 误伤，故显式放行（无真实网络 IO）。
    """
    if "integration" in request.keywords or "inproc_asgi" in request.keywords:
        return
    try:
        import pytest_socket  # noqa: PLC0415  (可选依赖)

        pytest_socket.disable_socket()
    except ImportError:
        pass  # 本地最小环境未装 pytest-socket 时降级为不断网（CI 必装）
