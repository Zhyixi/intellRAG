"""Langfuse tracing helpers for FastAPI + LangGraph endpoints."""
from __future__ import annotations

import logging
import uuid
from contextlib import contextmanager, nullcontext
from typing import Any, Iterator, Optional

from langfuse import get_client, propagate_attributes
from langfuse.langchain import CallbackHandler
from langfuse.types import TraceContext

_LANGFUSE_HANDLERS = (CallbackHandler,)

from configs.config import LANGFUSE_CONFIGURED, LANGFUSE_TRACING_ENVIRONMENT

logger = logging.getLogger(__name__)


def langfuse_configured() -> bool:
    """Return True when Langfuse API keys are present."""
    return LANGFUSE_CONFIGURED


def get_langfuse_prompt(client: Any, name: str, **kwargs: Any) -> Any:
    """Fetch a managed prompt from Langfuse, or raise if tracing is disabled."""
    if not langfuse_configured() or client is None:
        raise RuntimeError("Langfuse credentials not configured")
    return client.get_prompt(name, **kwargs)


def compile_langfuse_prompt_or_fallback(
    client: Any,
    name: str,
    *,
    fallback: str,
    label: str = "production",
    **compile_kwargs: Any,
) -> str:
    """Return compiled Langfuse prompt text, or a local fallback template."""
    try:
        return get_langfuse_prompt(client, name, label=label).compile(**compile_kwargs)
    except Exception as exc:
        logger.info("Langfuse prompt %s unavailable: %s", name, exc)
        if compile_kwargs:
            try:
                return fallback.format(**compile_kwargs)
            except (KeyError, IndexError, ValueError):
                return fallback
        return fallback


def langfuse_environment() -> str:
    """Deployment environment tag on Langfuse traces (dev / prod)."""
    return LANGFUSE_TRACING_ENVIRONMENT


def new_trace_id() -> str:
    return uuid.uuid4().hex


def new_run_id() -> str:
    return str(uuid.uuid4())


def get_active_trace_context() -> tuple[Optional[str], Optional[str]]:
    """Return (trace_id, parent_span_id) from the current @observe / span context."""
    if not langfuse_configured():
        return None, None
    try:
        client = get_client()
        trace_id = client.get_current_trace_id()
        if not trace_id:
            return None, None
        parent_span_id = client.get_current_observation_id()
        return str(trace_id), str(parent_span_id) if parent_span_id else None
    except Exception as exc:
        logger.debug("langfuse active trace context unavailable: %s", exc)
        return None, None


def iter_langfuse_handlers(config: Optional[dict]) -> list[CallbackHandler]:
    """Collect Langfuse CallbackHandler instances from a RunnableConfig."""
    if not config:
        return []
    callbacks = config.get("callbacks")
    handlers: list[CallbackHandler] = []
    if isinstance(callbacks, list):
        handlers.extend(cb for cb in callbacks if isinstance(cb, _LANGFUSE_HANDLERS))
    elif callbacks is not None:
        for attr in ("handlers", "inheritable_handlers"):
            group = getattr(callbacks, attr, None)
            if isinstance(group, list):
                handlers.extend(cb for cb in group if isinstance(cb, _LANGFUSE_HANDLERS))
    return handlers


def resolve_chain_observation_id(
    config: Optional[dict],
    *,
    node_name: str,
) -> Optional[str]:
    """Resolve the Langfuse observation id for an active LangGraph node span."""
    for handler in iter_langfuse_handlers(config):
        runs = getattr(handler, "_runs", None) or {}
        for observation in runs.values():
            if getattr(observation, "name", None) == node_name:
                observation_id = getattr(observation, "id", None)
                if observation_id:
                    return str(observation_id)
    return None


def create_langfuse_handler(
    trace_id: str,
    parent_span_id: Optional[str] = None,
) -> Optional[CallbackHandler]:
    """Bind LangChain callbacks to the active API trace."""
    if not langfuse_configured():
        return None
    trace_context: TraceContext = {"trace_id": trace_id}
    if parent_span_id:
        trace_context["parent_span_id"] = parent_span_id
    return CallbackHandler(trace_context=trace_context)


def resolve_trace_id(config: dict) -> str:
    """Resolve trace id from run metadata or Langfuse callback handler."""
    metadata = config.get("metadata") or {}
    trace_id = metadata.get("trace_id")
    if trace_id:
        return str(trace_id)

    callbacks = config.get("callbacks") or []
    if isinstance(callbacks, list):
        for callback in callbacks:
            trace = getattr(callback, "trace", None)
            trace_obj_id = getattr(trace, "id", None)
            if trace_obj_id:
                return str(trace_obj_id)
            last_trace_id = getattr(callback, "last_trace_id", None)
            if last_trace_id:
                return str(last_trace_id)

    run_id = config.get("run_id")
    if run_id:
        return str(run_id)
    return new_trace_id()


@contextmanager
def propagate_run_attributes(
    *,
    session_id: Optional[str] = None,
    trace_name: Optional[str] = None,
    trace_id: Optional[str] = None,
    environment: Optional[str] = None,
    domain: Optional[str] = None,
    user_id: Optional[str] = None,
) -> Iterator[None]:
    metadata: dict[str, str] = {}
    if trace_id:
        metadata["trace_id"] = str(trace_id)
    resolved_env = environment or langfuse_environment()
    if resolved_env:
        metadata["app_env"] = str(resolved_env)
    if domain:
        metadata["domain"] = str(domain)

    kwargs: dict[str, Any] = {}
    if session_id:
        kwargs["session_id"] = str(session_id)
    if trace_name:
        kwargs["trace_name"] = str(trace_name)
    if user_id:
        kwargs["user_id"] = str(user_id)
    if metadata:
        kwargs["metadata"] = metadata
    if resolved_env:
        kwargs["tags"] = [f"env:{resolved_env}"]

    ctx = propagate_attributes(**kwargs) if langfuse_configured() else nullcontext()
    with ctx:
        yield


def build_agent_run_config(
    *,
    session_id: str,
    retrice_service: Any,
    tb_repo: Any,
    llm: Any,
    translator: Optional[Any],
    version: str,
    syslang: str,
    intent: Optional[str],
    trace_name: str,
    run_id: Optional[str] = None,
    trace_id: Optional[str] = None,
    domain: Optional[str] = None,
    user_role: Optional[str] = None,
    user_id: Optional[str] = None,
) -> dict:
    """Build a LangGraph RunnableConfig with consistent Langfuse metadata."""
    active_trace_id, active_parent_span_id = get_active_trace_context()
    resolved_trace_id = trace_id or active_trace_id or new_trace_id()
    resolved_parent_span_id = active_parent_span_id if trace_id is None else None
    resolved_run_id = run_id or new_run_id()
    normalized_syslang = str(syslang or "zh").lower()

    metadata: dict[str, Any] = {
        "langfuse_trace_name": trace_name,
        "trace_id": resolved_trace_id,
        "session_id": session_id,
        "app_env": langfuse_environment(),
        "api_version": version,
    }
    if domain:
        metadata["domain"] = domain
    if user_role:
        metadata["user_role"] = user_role
    if user_id:
        metadata["user_id"] = user_id
    if intent is not None and intent != "":
        metadata["intent"] = intent

    callbacks: list[CallbackHandler] = []
    handler = create_langfuse_handler(resolved_trace_id, resolved_parent_span_id)
    if handler is not None:
        callbacks.append(handler)

    return {
        "configurable": {
            "thread_id": session_id,
            "session_id": session_id,
            "retrice_service": retrice_service,
            "tb_repo": tb_repo,
            "llm": llm,
            "translator": translator,
            "version": version,
            "syslang": normalized_syslang,
            "intent": intent,
        },
        "callbacks": callbacks,
        "run_id": resolved_run_id,
        "metadata": metadata,
    }


@contextmanager
def langgraph_chain_observation(
    *,
    name: str,
    trace_id: Optional[str] = None,
    parent_span_id: Optional[str] = None,
    input: Optional[Any] = None,
    metadata: Optional[dict[str, Any]] = None,
) -> Iterator[dict[str, Optional[str]]]:
    """Nest a LangGraph invoke under the active API trace as a chain span."""
    if not langfuse_configured():
        yield {
            "trace_id": str(trace_id or new_trace_id()),
            "parent_span_id": parent_span_id,
            "observation": None,
        }
        return

    client = get_client()
    active_trace_id, active_parent_span_id = get_active_trace_context()
    resolved_trace_id = str(trace_id or active_trace_id or new_trace_id())
    resolved_parent_span_id = parent_span_id or active_parent_span_id

    observation_metadata = dict(metadata or {})
    observation_metadata.setdefault("trace_id", resolved_trace_id)

    trace_context: TraceContext = {"trace_id": resolved_trace_id}
    if resolved_parent_span_id:
        trace_context["parent_span_id"] = str(resolved_parent_span_id)

    with client.start_as_current_observation(
        name=name,
        as_type="chain",
        trace_context=trace_context,
        input=input,
        metadata=observation_metadata,
    ) as observation:
        child_trace_id, child_parent_span_id = get_active_trace_context()
        yield {
            "trace_id": child_trace_id or resolved_trace_id,
            "parent_span_id": child_parent_span_id,
            "observation": observation,
        }


def build_langgraph_invoke_config(
    *,
    trace_id: str,
    parent_span_id: Optional[str],
    run_id: Optional[str] = None,
    session_id: Optional[str] = None,
    trace_name: Optional[str] = None,
    configurable: Optional[dict[str, Any]] = None,
    extra_metadata: Optional[dict[str, Any]] = None,
) -> dict:
    """RunnableConfig fragment for a nested LangGraph under the current trace."""
    metadata: dict[str, Any] = {
        "trace_id": str(trace_id),
        "langfuse_trace_name": trace_name or "langgraph_invoke",
    }
    if session_id:
        metadata["session_id"] = str(session_id)
    if extra_metadata:
        metadata.update(extra_metadata)

    callbacks: list[CallbackHandler] = []
    handler = create_langfuse_handler(str(trace_id), parent_span_id)
    if handler is not None:
        callbacks.append(handler)

    config: dict[str, Any] = {
        "callbacks": callbacks,
        "run_id": run_id or new_run_id(),
        "metadata": metadata,
    }
    if configurable is not None:
        config["configurable"] = configurable
    return config


def flush_langfuse_client(langfuse_client: Any) -> None:
    if not langfuse_configured() or langfuse_client is None:
        return
    try:
        langfuse_client.flush()
    except Exception as exc:
        logger.debug("langfuse flush skipped: %s", exc)


@contextmanager
def observe_tool_span(
    *,
    name: str,
    input: Any = None,
    metadata: Optional[dict[str, Any]] = None,
) -> Iterator[Any]:
    """Record a Langfuse tool span (e.g. web search MCP tool) under the active trace."""
    if not langfuse_configured():
        yield None
        return

    trace_id, parent_span_id = get_active_trace_context()
    if not trace_id:
        yield None
        return

    client = get_client()
    trace_context: TraceContext = {"trace_id": trace_id}
    if parent_span_id:
        trace_context["parent_span_id"] = parent_span_id

    with client.start_as_current_observation(
        as_type="tool",
        name=name,
        trace_context=trace_context,
        input=input,
        metadata=metadata or {},
    ) as observation:
        yield observation


def flush_langfuse() -> None:
    """Flush the process-global Langfuse client (e.g. after background jobs)."""
    if not langfuse_configured():
        return
    try:
        flush_langfuse_client(get_client())
    except Exception as exc:
        logger.debug("langfuse flush skipped: %s", exc)


def estimate_embedding_tokens(texts: list[str]) -> int:
    """Rough token estimate for embedding usage/cost when provider omits counts."""
    if not texts:
        return 0
    return max(1, sum(len(t) for t in texts) // 4)


def _embedding_model_name(model: Any) -> str:
    for attr in ("model", "model_name"):
        value = getattr(model, attr, None)
        if value:
            return str(value)
    return type(model).__name__


class LangfuseObservedEmbeddings:
    """Wrap LangChain Embeddings so each embed call emits a Langfuse generation."""

    def __init__(self, inner: Any):
        self._inner = inner

    def embed_documents(self, texts: list[str], **kwargs: Any) -> list[list[float]]:
        if not langfuse_configured() or not texts:
            return self._inner.embed_documents(texts, **kwargs)
        trace_id, parent_span_id = get_active_trace_context()
        if not trace_id:
            return self._inner.embed_documents(texts, **kwargs)

        client = get_client()
        trace_context: TraceContext = {"trace_id": trace_id}
        if parent_span_id:
            trace_context["parent_span_id"] = parent_span_id

        model = _embedding_model_name(self._inner)
        if len(texts) == 1:
            generation_input: Any = texts[0][:500]
        else:
            generation_input = {
                "batch_size": len(texts),
                "char_count": sum(len(t) for t in texts),
            }

        with client.start_as_current_observation(
            as_type="generation",
            name="embed_documents",
            trace_context=trace_context,
            model=model,
            input=generation_input,
            metadata={"text_count": len(texts)},
        ) as generation:
            try:
                vectors = self._inner.embed_documents(texts, **kwargs)
                tokens = estimate_embedding_tokens(texts)
                generation.update(
                    output={
                        "vector_count": len(vectors),
                        "dimensions": len(vectors[0]) if vectors else 0,
                    },
                    usage={"input": tokens, "output": 0, "total": tokens},
                )
                return vectors
            except Exception as exc:
                generation.update(level="ERROR", status_message=str(exc))
                raise

    def embed_query(self, text: str, **kwargs: Any) -> list[float]:
        if not langfuse_configured():
            return self._inner.embed_query(text, **kwargs)
        trace_id, parent_span_id = get_active_trace_context()
        if not trace_id:
            return self._inner.embed_query(text, **kwargs)

        client = get_client()
        trace_context: TraceContext = {"trace_id": trace_id}
        if parent_span_id:
            trace_context["parent_span_id"] = parent_span_id

        model = _embedding_model_name(self._inner)
        with client.start_as_current_observation(
            as_type="generation",
            name="embed_query",
            trace_context=trace_context,
            model=model,
            input=text[:500],
            metadata={"text_count": 1},
        ) as generation:
            try:
                vector = self._inner.embed_query(text, **kwargs)
                tokens = estimate_embedding_tokens([text])
                generation.update(
                    output={"dimensions": len(vector)},
                    usage={"input": tokens, "output": 0, "total": tokens},
                )
                return vector
            except Exception as exc:
                generation.update(level="ERROR", status_message=str(exc))
                raise

    def __getattr__(self, name: str) -> Any:
        return getattr(self._inner, name)


def wrap_embedding_model_for_tracing(embedding_model: Any) -> Any:
    """Return a Langfuse-instrumented embeddings wrapper when tracing is enabled."""
    if not langfuse_configured() or isinstance(embedding_model, LangfuseObservedEmbeddings):
        return embedding_model
    return LangfuseObservedEmbeddings(embedding_model)


@contextmanager
def embedding_page_observation(
    *,
    page_num: int,
    page_index: int,
    total_pages: int,
    doc_id: str,
    filename: str,
    char_count: int,
) -> Iterator[None]:
    """Nest a per-page span under the active embedding job trace."""
    if not langfuse_configured():
        yield
        return

    trace_id, parent_span_id = get_active_trace_context()
    if not trace_id:
        yield
        return

    client = get_client()
    trace_context: TraceContext = {"trace_id": trace_id}
    if parent_span_id:
        trace_context["parent_span_id"] = parent_span_id

    with client.start_as_current_observation(
        name="notebook_embed_page",
        as_type="span",
        trace_context=trace_context,
        input={"page": page_num, "chars": char_count},
        metadata={
            "doc_id": doc_id,
            "filename": filename,
            "page_index": page_index,
            "total_pages": total_pages,
        },
    ):
        yield
