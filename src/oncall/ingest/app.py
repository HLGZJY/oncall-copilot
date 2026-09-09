"""FastAPI 入口：POST /ingest（Alertmanager receiver 的目标端点）+ 事件卡片查询。

本票只落 oncall 侧端点；deploy/ 的 dump receiver 与双写开关不动（切换在 issue 06）。

dev 运行（仓库根目录）：
    uvicorn oncall.ingest.app:create_app --factory --port 8000
数据库 URL 经环境变量 ONCALL_DATABASE_URL 配置，默认 sqlite:///./oncall.db。
"""

from __future__ import annotations

import dataclasses
import os
from datetime import timedelta
from typing import TYPE_CHECKING

from fastapi import FastAPI
from sqlalchemy import create_engine
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session

from oncall.api.card import build_alert_card
from oncall.api.investigation import (
    InvestigationDeps,
    create_investigation_router,
)
from oncall.api.kb import create_kb_router
from oncall.api.remediation import RemediationDeps, create_remediation_router
from oncall.api.routes import create_router
from oncall.context.config import ContextConfig
from oncall.db import create_tables
from oncall.ingest.fingerprint import DEFAULT_DEDUP_WINDOW
from oncall.ingest.schemas import AlertmanagerWebhook
from oncall.ingest.service import ingest_webhook

if TYPE_CHECKING:
    from oncall.classify.service import ClassifyRuntime
    from oncall.context.promql import PromClient
    from oncall.knowledge.pipeline import KnowledgePipeline

DATABASE_URL_ENV = "ONCALL_DATABASE_URL"
DEFAULT_DATABASE_URL = "sqlite:///./oncall.db"
DEDUP_WINDOW_SECONDS_ENV = "ONCALL_DEDUP_WINDOW_SECONDS"
KB_ENABLED_ENV = "ONCALL_KB_ENABLED"  # M6-T4：知识库装配开关（缺省关，测试零影响）
KB_EMBEDDER_ENV = "ONCALL_KB_EMBEDDER"  # mock | bge（真实模型，T7 实测票用）
KB_CHROMA_PATH_ENV = "ONCALL_CHROMA_PATH"  # 设置 → ChromaVectorStore；缺省内存索引


def create_app(  # noqa: PLR0913, PLR0917 — 注入面持续增长（M2 classify → M3 investigation）；
    # 收拢进单个 dataclass 会破坏 M1/M2 既有测试的 create_app 关键字调用面（票面边界不改 M2 文件）
    engine: Engine | None = None,
    dedup_window: timedelta | None = None,
    context_config: ContextConfig | None = None,
    context_client: PromClient | None = None,
    classify_runtime: ClassifyRuntime | None = None,
    investigation: InvestigationDeps | None = None,
    remediation: RemediationDeps | None = None,
    kb_pipeline: KnowledgePipeline | None = None,
) -> FastAPI:
    """应用工厂：测试注入内存库引擎与上下文替身；进程启动走环境变量配置。

    时间窗默认 10 分钟（G1），可用 `ONCALL_DEDUP_WINDOW_SECONDS` 覆盖；
    上下文配置默认 `ContextConfig.from_env()`（issue 04 口径）。
    `classify_runtime` 透传给 /classify（G4 独立入口）：不注入时该端点落 503，
    本工厂自身保持零 LLM 运行时依赖（类型仅 TYPE_CHECKING 引用，R6）。

    issue 07 起：未显式注入时按环境变量装配**真实** LLM 通道
    （`_classify_runtime_from_env`），配置不全则维持 None（/classify 落 503，
    ingest 主链路照常可用）——装配失败不拖垮 ingest 是 R6 的硬要求。

    issue 07（M3/T7）：`investigation` 透传给 /investigate 与
    /investigations/{id}（组件缺省 None → /investigate 落 503，不静默降级到
    mock；真实 Planner client 属 T8）；opening_builder 缺省由本工厂按
    context 配置兜底（D-17 事件卡片，时间锚 last_fired_at）。
    """
    if engine is None:
        engine = create_engine(os.environ.get(DATABASE_URL_ENV, DEFAULT_DATABASE_URL))
    if dedup_window is None:
        dedup_window = timedelta(
            seconds=int(
                os.environ.get(DEDUP_WINDOW_SECONDS_ENV, int(DEFAULT_DEDUP_WINDOW.total_seconds()))
            )
        )
    if context_config is None:
        context_config = ContextConfig.from_env()
    if classify_runtime is None:
        classify_runtime = _classify_runtime_from_env()
    create_tables(engine)  # dev 建表（D-13：Alembic 延至切 MySQL 时引入）

    app = FastAPI(title="oncall-copilot", version="0.1.0")

    @app.post("/ingest")
    def ingest(webhook: AlertmanagerWebhook) -> dict[str, int]:
        """接收 Alertmanager webhook v4 payload，归一化落 alert_events。

        成功 2xx + {received, deduped}；校验失败由 FastAPI 落 422（4xx 不吞错，
        让 AM 的重试语义可见异常）。
        """
        with Session(engine) as session:
            result = ingest_webhook(session, webhook, dedup_window)
        return {"received": result.received, "deduped": result.deduped}

    # 事件卡片查询路由 + 分类/事件查询（issue 05 / issue 04）：context_client 与
    # classify_runtime 为 None 时对应端点按各自语义降级（collect_context 显式
    # unavailable / /classify 落 503）；测试注入替身避免真实网络与真实 LLM
    app.include_router(
        create_router(
            engine,
            context_config=context_config,
            context_client=context_client,
            classify_runtime=classify_runtime,
        )
    )

    # 调查入口路由（issue 07 / M3-T7）：组件缺省 None → /investigate 落 503；
    # opening_builder 缺省按 context 配置组装 D-17 卡片（时间锚 last_fired_at）
    if investigation is None:
        investigation = InvestigationDeps(components=None)
    if investigation.opening_builder is None:
        investigation = _with_default_opening_builder(investigation, context_config, context_client)
    app.include_router(create_investigation_router(engine, investigation))

    # 确认门路由（M5 issue 04 / T4 / D-40/D-48）：执行器/验证器缺省 None →
    # confirm approve 落 503 降级（proposal 留 approved 即终）；GET 查询照常可用
    if remediation is None:
        remediation = RemediationDeps()

    # 知识库装配（M6-T4 / D-51–D-57）：env 开关缺省关——关闭时 kb_pipeline 为
    # None（/kb/ingest 落 503、query_kb 落 stub、开局召回不启用），既有测试零影响
    kb_enabled = os.environ.get(KB_ENABLED_ENV, "").strip().lower() in {"1", "true", "yes"}
    if kb_enabled and kb_pipeline is None:
        kb_pipeline = _kb_pipeline_from_env(engine)
    if kb_pipeline is not None:
        # D-56：recovered 后同步触发入库（best-effort，失败落日志不阻塞处置出口）
        remediation = dataclasses.replace(remediation, kb_pipeline=kb_pipeline)
        if investigation is not None and investigation.kb_retriever is None:
            investigation = dataclasses.replace(investigation, kb_retriever=kb_pipeline.retriever())
    app.include_router(create_remediation_router(engine, remediation))
    app.include_router(create_kb_router(engine, kb_pipeline))

    return app


def _kb_pipeline_from_env(engine: Engine) -> KnowledgePipeline:
    """按环境变量装配知识管线（M6-T4；局部导入保「没配 KB 也能起」R6 语义）。

    embedder：`ONCALL_KB_EMBEDDER=bge` 走本地 bge-small-zh-v1.5（D-51，T7 实测票
    才默认启用——首载需下载模型）；缺省 mock（确定性向量，功能链路可验证）。
    store：`ONCALL_CHROMA_PATH` 设置 → Chroma persistent（D-52）；缺省内存索引
    （权威在 kb_chunks 表，可重建，D-55）。
    """
    from oncall.knowledge.embedder import MockEmbedder  # noqa: PLC0415
    from oncall.knowledge.pipeline import KnowledgePipeline  # noqa: PLC0415
    from oncall.knowledge.store import InMemoryVectorStore  # noqa: PLC0415

    embedder: object = MockEmbedder()
    if os.environ.get(KB_EMBEDDER_ENV, "mock").strip().lower() == "bge":
        # 真实模型走 key/环境门槛票（T7）
        from sentence_transformers import SentenceTransformer  # noqa: PLC0415

        model = SentenceTransformer("BAAI/bge-small-zh-v1.5")

        class _BgeEmbedder:
            dimension = 512

            def embed(self, texts: list[str]) -> list[list[float]]:
                return model.encode(texts, normalize_embeddings=True).tolist()

        embedder = _BgeEmbedder()
    store = InMemoryVectorStore()
    chroma_path = os.environ.get(KB_CHROMA_PATH_ENV)
    if chroma_path:
        from oncall.knowledge.store import ChromaVectorStore  # noqa: PLC0415

        store = ChromaVectorStore(path=chroma_path)

    pipeline = KnowledgePipeline(engine, embedder, store)  # type: ignore[arg-type]
    return pipeline


def _with_default_opening_builder(
    deps: InvestigationDeps,
    context_config: ContextConfig,
    context_client: PromClient | None,
) -> InvestigationDeps:
    """补装默认开局锚点构造器：incident.alert_ids[0] → D-17 事件卡片。

    独立小工厂而非闭包内联：dataclasses.replace 语义直观，且便于测试直接
    断言「卡片以 alert_ids[0] 组装、时间锚 last_fired_at」。
    """

    def build(session: Session, alert_id: int) -> dict[str, object] | None:
        return build_alert_card(session, alert_id, config=context_config, client=context_client)

    return dataclasses.replace(deps, opening_builder=build)


def _classify_runtime_from_env() -> ClassifyRuntime | None:
    """按环境变量装配真实 LLM 通道（issue 07）；配置不全返回 None → /classify 503。

    局部导入而非模块级：本模块必须保持「没装 / 没配 LLM 也能起 ingest」的
    可用性（R6：ingest 主链路零 LLM 依赖），SDK 依赖链只在确需装配时引入。
    """
    from oncall.classify.llm import LLMChannel, LLMChannelOptions  # noqa: PLC0415
    from oncall.classify.service import ClassifyRuntime  # noqa: PLC0415
    from oncall.infra.llm import LLMConfigError, OpenAILLMClassifier  # noqa: PLC0415

    try:
        classifier = OpenAILLMClassifier.from_env()
    except LLMConfigError:
        return None  # 未配置真实 LLM：/classify 落 503，不静默降级到 mock
    return ClassifyRuntime(
        llm_channel=LLMChannel(
            classifier,
            model=classifier.model,
            samples=classifier.samples,  # 与 client 同批样本，prompt 一致
            options=LLMChannelOptions(timeout_seconds=classifier.timeout_seconds),
        )
    )
