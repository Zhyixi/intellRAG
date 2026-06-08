"""Notebook chat and history API."""
from __future__ import annotations

import datetime
import json
import logging
import uuid

import pymongo
from dependency_injector.wiring import Provide, inject
from fastapi import APIRouter, Depends, HTTPException, status
from fastapi.responses import StreamingResponse
from langfuse import observe
from langgraph.checkpoint.mongodb import MongoDBSaver
from sqlalchemy.orm import Session

from common.auth import get_current_user
from common.langfuse_tracing import build_agent_run_config, propagate_run_attributes
from common.utils import custom_jsonable_encoder, replace_invalid_values
from containers import Container, get_container
from database.database import Database
from fast_api_service.api.notebook.schemas import NotebookChatRequest, NotebookChatResponse
from models.user_models import User
from services.llm_factory import create_chat_llm, resolve_openai_api_key
from services.memory_service import MemoryService
from services.notebook_graphs import process_notebook_chat, stream_notebook_chat
from services.rag_service import RetriveService
from services.services import MongoService

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/v1/notebook", tags=["iap-notebook"])

NB_CHAT_HISTORY = "nb_chat_history"


def _get_db_session():
    db: Database = get_container().sql_db()
    with db.session() as session:
        yield session


def _build_history_payload(
    *,
    user_id: int,
    session_id: str,
    user_input: str,
    final_state: dict,
    started_at: datetime.datetime,
    elapsed_seconds: float,
) -> dict:
    return {
        "user_id": user_id,
        "session_id": session_id,
        "timestamp": started_at,
        "Question": user_input,
        "Response": final_state.get("final_response", ""),
        "Results": final_state.get("results", []),
        "ret_type": "notebook",
        "Time-Consuming": str(elapsed_seconds),
    }


def _validate_chat_request(body: NotebookChatRequest) -> str:
    content = (body.content or "").strip()
    if not content and not body.confirm_web_search:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="content is required",
        )
    return content


def _prepare_notebook_run(
    *,
    body: NotebookChatRequest,
    current_user: User,
    session: Session,
    retrice_service: RetriveService,
    mongo_service: MongoService,
    streaming: bool,
    checkpointer: MongoDBSaver,
    langfuse_client=None,
) -> tuple[str, str, object, MemoryService, MongoDBSaver, dict]:
    session_id = body.session_id or str(uuid.uuid4())
    user_input = _validate_chat_request(body)

    api_key = resolve_openai_api_key(session, current_user.id)
    llm = create_chat_llm(api_key, streaming=streaming)
    retrice_service.vllm_general_llm = llm
    retrice_service.ollama_general_llm = llm
    memory_service = MemoryService(mongo_service)

    run_config = build_agent_run_config(
        session_id=session_id,
        retrice_service=retrice_service,
        tb_repo=None,
        llm=llm,
        translator=None,
        version="v1",
        syslang=body.syslang.lower()[:2] if body.syslang else "zh",
        intent="notebook",
        trace_name="notebook_chat",
        domain="notebook",
        user_role=str(current_user.id),
        user_id=str(current_user.id),
    )
    run_config["configurable"]["llm"] = llm
    run_config["configurable"]["memory_service"] = memory_service
    run_config["configurable"]["user_id"] = str(current_user.id)
    run_config["configurable"]["session_id"] = session_id
    run_config["configurable"]["confirm_web_search"] = body.confirm_web_search
    run_config["configurable"]["web_search_query"] = body.web_search_query
    run_config["configurable"]["langfuse_client"] = langfuse_client
    run_config["configurable"]["langfuse_prompt_label"] = body.langfuse_prompt_label or "production"

    return session_id, user_input, llm, memory_service, checkpointer, run_config


async def _persist_chat_turn(
    *,
    mongo_service: MongoService,
    memory_service: MemoryService,
    current_user: User,
    session_id: str,
    user_input: str,
    final_state: dict,
    llm,
    started_at: datetime.datetime,
) -> None:
    elapsed = (datetime.datetime.now() - started_at).total_seconds()
    mongo_service.insert_many(
        insert_data=_build_history_payload(
            user_id=current_user.id,
            session_id=session_id,
            user_input=user_input or final_state.get("pending_web_search_query", ""),
            final_state=final_state,
            started_at=started_at,
            elapsed_seconds=elapsed,
        ),
        collection_name=NB_CHAT_HISTORY,
    )

    hist_df = mongo_service.find_df(
        query={"session_id": session_id, "user_id": current_user.id},
        collection_name=NB_CHAT_HISTORY,
    )
    turn_count = len(hist_df) if not hist_df.empty else 1
    await memory_service.maybe_extract_memory(
        user_id=current_user.id,
        session_id=session_id,
        user_input=user_input or final_state.get("pending_web_search_query", ""),
        assistant_reply=final_state.get("final_response", ""),
        llm=llm,
        turn_count=turn_count,
    )


@router.post("/chat/retrieve", response_model=NotebookChatResponse)
@observe(name="notebook_chat")
@inject
async def notebook_chat(
    body: NotebookChatRequest,
    current_user: User = Depends(get_current_user),
    mongo_service: MongoService = Depends(Provide[Container.mongo_service]),
    retrice_service: RetriveService = Depends(Provide[Container.retrive_service]),
    session: Session = Depends(_get_db_session),
    checkpointer: MongoDBSaver = Depends(Provide[Container.mongo_checkpointer]),
    langfuse_client=Depends(Provide[Container.langfuse_client]),
):
    session_id, user_input, llm, memory_service, checkpointer, run_config = _prepare_notebook_run(
        body=body,
        current_user=current_user,
        session=session,
        retrice_service=retrice_service,
        mongo_service=mongo_service,
        streaming=False,
        checkpointer=checkpointer,
        langfuse_client=langfuse_client,
    )

    started_at = datetime.datetime.now()
    with propagate_run_attributes(
        session_id=session_id,
        trace_name="notebook_chat",
        domain="notebook",
        user_id=str(current_user.id),
    ):
        final_state = await process_notebook_chat(user_input, run_config, checkpointer)

    await _persist_chat_turn(
        mongo_service=mongo_service,
        memory_service=memory_service,
        current_user=current_user,
        session_id=session_id,
        user_input=user_input,
        final_state=final_state,
        llm=llm,
        started_at=started_at,
    )

    return NotebookChatResponse(
        chat_response=final_state.get("final_response", ""),
        results=final_state.get("results", []),
        source=final_state.get("source", []),
        suggested_questions=final_state.get("suggested_questions", []),
        offer_web_search=bool(final_state.get("offer_web_search")),
        pending_web_search_query=final_state.get("pending_web_search_query", "") or "",
        needs_clarification=bool(final_state.get("needs_clarification")),
        answered_from_memory=bool(final_state.get("answered_from_memory")),
    )


@router.post("/chat/stream")
@observe(name="notebook_chat_stream")
@inject
async def notebook_chat_stream(
    body: NotebookChatRequest,
    current_user: User = Depends(get_current_user),
    mongo_service: MongoService = Depends(Provide[Container.mongo_service]),
    retrice_service: RetriveService = Depends(Provide[Container.retrive_service]),
    session: Session = Depends(_get_db_session),
    checkpointer: MongoDBSaver = Depends(Provide[Container.mongo_checkpointer]),
    langfuse_client=Depends(Provide[Container.langfuse_client]),
):
    session_id, user_input, llm, memory_service, checkpointer, run_config = _prepare_notebook_run(
        body=body,
        current_user=current_user,
        session=session,
        retrice_service=retrice_service,
        mongo_service=mongo_service,
        streaming=True,
        checkpointer=checkpointer,
        langfuse_client=langfuse_client,
    )
    started_at = datetime.datetime.now()

    async def event_generator():
        final_state: dict = {}
        try:
            with propagate_run_attributes(
                session_id=session_id,
                trace_name="notebook_chat_stream",
                domain="notebook",
                user_id=str(current_user.id),
            ):
                async for event in stream_notebook_chat(user_input, run_config, checkpointer):
                    if event.get("type") == "done":
                        final_state = {
                            "final_response": event.get("chat_response", ""),
                            "results": event.get("results", []),
                            "source": event.get("source", []),
                            "suggested_questions": event.get("suggested_questions", []),
                            "offer_web_search": event.get("offer_web_search", False),
                            "pending_web_search_query": event.get(
                                "pending_web_search_query", ""
                            ),
                            "needs_clarification": event.get("needs_clarification", False),
                            "answered_from_memory": event.get(
                                "answered_from_memory", False
                            ),
                        }
                    yield f"data: {json.dumps(event, ensure_ascii=False)}\n\n"
        except Exception as exc:
            logger.exception("notebook stream failed: %s", exc)
            err = {"type": "error", "message": str(exc)}
            yield f"data: {json.dumps(err, ensure_ascii=False)}\n\n"
            return

        if final_state:
            await _persist_chat_turn(
                mongo_service=mongo_service,
                memory_service=memory_service,
                current_user=current_user,
                session_id=session_id,
                user_input=user_input,
                final_state=final_state,
                llm=llm,
                started_at=started_at,
            )

    return StreamingResponse(
        event_generator(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


@router.get("/history/session_ids")
@inject
async def history_session_ids(
    current_user: User = Depends(get_current_user),
    mongo_service: MongoService = Depends(Provide[Container.mongo_service]),
    limit: int = 20,
):
    pipeline = [
        {"$match": {"user_id": current_user.id}},
        {"$sort": {"timestamp": -1}},
        {
            "$group": {
                "_id": "$session_id",
                "Question": {"$first": "$Question"},
                "timestamp": {"$first": "$timestamp"},
            }
        },
        {"$sort": {"timestamp": -1}},
        {"$limit": limit},
    ]
    df = mongo_service.aggregate_to_df(pipeline=pipeline, collection_name=NB_CHAT_HISTORY)
    if df.empty:
        return []
    rows = []
    for _, row in df.iterrows():
        rows.append([row["_id"], row.get("Question", ""), row.get("timestamp")])
    return rows


@router.get("/history/session")
@inject
async def history_session(
    session_id: str,
    current_user: User = Depends(get_current_user),
    mongo_service: MongoService = Depends(Provide[Container.mongo_service]),
):
    df = mongo_service.find_df(
        query={"session_id": session_id, "user_id": current_user.id},
        collection_name=NB_CHAT_HISTORY,
        sort=[("timestamp", pymongo.ASCENDING)],
    )
    if df.empty:
        return {"result": []}
    if "_id" in df.columns:
        df.drop(columns=["_id"], inplace=True)
    df = df.map(lambda x: custom_jsonable_encoder(x))
    df = replace_invalid_values(df)
    return {"result": df.to_dict(orient="records")}


@router.delete("/history/session")
@inject
async def delete_history_session(
    session_id: str,
    current_user: User = Depends(get_current_user),
    mongo_service: MongoService = Depends(Provide[Container.mongo_service]),
    checkpointer: MongoDBSaver = Depends(Provide[Container.mongo_checkpointer]),
):
    mongo_service.delete_many(
        query={"session_id": session_id, "user_id": current_user.id},
        collection_name=NB_CHAT_HISTORY,
    )
    try:
        checkpointer.delete_thread(session_id)
    except Exception as exc:
        logger.warning("delete_thread failed for %s: %s", session_id, exc)
    return {"message": "deleted", "session_id": session_id}
