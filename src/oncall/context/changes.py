"""近期变更源（G4 占位）：M0 未提供变更数据源，返回空列表不报错、不阻塞。

接口形状与另两源对齐（SourceResult：列表 + 来源标记）；未来接入真实
变更源（发布记录 / 配置 diff）时只改本 adapter，collect_context 不动。
"""

from __future__ import annotations

from oncall.context.config import ContextConfig
from oncall.context.models import SOURCE_CHANGES, STATUS_OK, SourceResult

PLACEHOLDER_NOTE = "占位 adapter：变更数据源未接入（G4 定案），接入前恒为空列表"


def recent_changes(*, config: ContextConfig | None = None) -> SourceResult:
    """恒返回 ok + 空列表；config 入参仅为签名对齐，占位期不消费。"""
    return SourceResult(SOURCE_CHANGES, STATUS_OK, (), {"note": PLACEHOLDER_NOTE})
