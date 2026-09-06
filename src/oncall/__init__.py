"""OnCall Copilot 核心包。

模块边界（import-linter C3/C4/C5）：
- harness 禁 import 业务模块（ingest/classify/remediation/knowledge/eval/api）
- HTTP 与 LLM SDK 只允许在 infra/ 收口
"""

__version__ = "0.1.0"
