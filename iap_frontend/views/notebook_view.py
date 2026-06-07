"""我的筆記本 — 聊天介面（流式 + 工具状态）。"""
from __future__ import annotations

import uuid

import streamlit as st

from HandleRequest.notebook import chat_stream, get_history_session, get_history_session_ids
from styles import custom_css
from views.strings import (
    BTN_NEW_CHAT,
    BTN_WEB_SEARCH,
    CHAT_HISTORY_LOADED,
    CHAT_INPUT_PLACEHOLDER,
    CHAT_REQUEST_FAILED,
    CHAT_THINKING,
    CHAT_TOOL_PREFIX,
    CHAT_WELCOME,
    CITATION_LINE,
    CITATIONS_TITLE,
    PLEASE_LOGIN,
    SUGGESTED_QUESTIONS_TITLE,
    WEB_SEARCH_HINT,
)


def _ensure_session_state():
    if "chat_history" not in st.session_state:
        st.session_state["chat_history"] = {}
    if "current_chat" not in st.session_state:
        st.session_state["current_chat"] = [
            {"role": "assistant", "content": CHAT_WELCOME},
        ]
    if "current_chat_id" not in st.session_state:
        st.session_state["current_chat_id"] = None
    if "suggested_questions" not in st.session_state:
        st.session_state["suggested_questions"] = []
    if "offer_web_search" not in st.session_state:
        st.session_state["offer_web_search"] = False
    if "pending_web_search_query" not in st.session_state:
        st.session_state["pending_web_search_query"] = ""


def _create_new_chat():
    st.session_state["current_chat"] = [{"role": "assistant", "content": CHAT_WELCOME}]
    st.session_state["current_chat_id"] = None
    st.session_state["suggested_questions"] = []
    st.session_state["offer_web_search"] = False
    st.session_state["pending_web_search_query"] = ""


def _load_chat_from_history(chat_id: str, records: list):
    st.session_state["current_chat_id"] = chat_id
    st.session_state["current_chat"] = [{"role": "assistant", "content": CHAT_HISTORY_LOADED}]
    for row in records:
        st.session_state["current_chat"].append({"role": "user", "content": row.get("Question", "")})
        st.session_state["current_chat"].append({"role": "assistant", "content": row.get("Response", "")})
    st.session_state["offer_web_search"] = False
    st.session_state["pending_web_search_query"] = ""


def _init_history_from_api(token: str):
    result = get_history_session_ids(token)
    if result["status_code"] != 200:
        return
    for item in result.get("res") or []:
        if not item:
            continue
        session_id = item[0]
        if session_id not in st.session_state["chat_history"]:
            hist = get_history_session(token, session_id)
            st.session_state["chat_history"][session_id] = hist.get("res", {}).get("result", [])


def _run_stream_chat(
    token: str,
    *,
    prompt: str,
    confirm_web_search: bool = False,
    web_search_query: str | None = None,
) -> dict:
    if st.session_state["current_chat_id"] is None:
        st.session_state["current_chat_id"] = str(uuid.uuid4())
        st.session_state["chat_history"][st.session_state["current_chat_id"]] = []

    session_id = st.session_state["current_chat_id"]
    if prompt:
        with st.chat_message("user"):
            st.markdown(prompt)
        st.session_state["current_chat"].append({"role": "user", "content": prompt})

    reply = ""
    results: list = []
    suggested: list = []
    offer_web = False
    pending_query = ""
    failed = False

    with st.chat_message("assistant"):
        status_line = st.empty()
        tools_box = st.empty()
        text_box = st.empty()
        tool_lines: list[str] = []

        status_line.caption(CHAT_THINKING)
        for event in chat_stream(
            token,
            session_id=session_id,
            content=prompt,
            confirm_web_search=confirm_web_search,
            web_search_query=web_search_query,
        ):
            etype = event.get("type")
            if etype == "error":
                reply = f"{CHAT_REQUEST_FAILED}: {event.get('message', '')}"
                failed = True
                break
            if etype in ("status", "node"):
                msg = event.get("message") or event.get("node") or ""
                status_line.caption(f"{CHAT_THINKING} — {msg}")
                tool = event.get("tool")
                detail = event.get("detail") or msg
                if tool:
                    tool_lines.append(f"{CHAT_TOOL_PREFIX}: **{tool}** — {detail}")
                    tools_box.markdown("\n".join(f"- {line}" for line in tool_lines))
            elif etype == "token":
                reply += event.get("content") or ""
                text_box.markdown(reply)
            elif etype == "done":
                reply = event.get("chat_response") or reply
                results = event.get("results") or []
                suggested = event.get("suggested_questions") or []
                offer_web = bool(event.get("offer_web_search"))
                pending_query = event.get("pending_web_search_query") or ""
                text_box.markdown(reply)
                status_line.empty()

    if not failed and not reply:
        reply = CHAT_REQUEST_FAILED
        failed = True

    st.session_state["current_chat"].append({"role": "assistant", "content": reply})
    if results:
        st.session_state["current_chat"].append(
            {"role": "assistant", "content": results, "citations": True}
        )
    st.session_state["suggested_questions"] = suggested if not failed else []
    st.session_state["offer_web_search"] = offer_web and not failed
    st.session_state["pending_web_search_query"] = pending_query if offer_web else ""

    sid = st.session_state["current_chat_id"]
    hist_prompt = prompt or web_search_query or pending_query or "(web search)"
    st.session_state["chat_history"].setdefault(sid, []).append(
        {"Question": hist_prompt, "Response": reply}
    )

    return {"failed": failed, "offer_web_search": offer_web}


def _submit_chat(
    token: str,
    prompt: str,
    *,
    confirm_web_search: bool = False,
    web_search_query: str | None = None,
) -> None:
    _run_stream_chat(
        token,
        prompt=prompt,
        confirm_web_search=confirm_web_search,
        web_search_query=web_search_query,
    )
    st.rerun()


def render_notebook_page(cookies):
    token = st.session_state.get("auth_token") or cookies.get("auth_token")
    if not token:
        st.warning(PLEASE_LOGIN)
        return

    _ensure_session_state()
    st.markdown(custom_css, unsafe_allow_html=True)
    _init_history_from_api(token)

    with st.sidebar:
        st.write(f"**{cookies.get('username', '')}**")
        st.caption(cookies.get("email", ""))
        if st.button(BTN_NEW_CHAT):
            _create_new_chat()
            st.rerun()
        for chat_id, records in st.session_state.get("chat_history", {}).items():
            label = chat_id[:8]
            if records:
                label = f"{records[0].get('Question', '')[:20]}…"
            if st.button(label, key=f"hist_{chat_id}"):
                _load_chat_from_history(chat_id, records)
                st.rerun()

    for msg in st.session_state["current_chat"]:
        with st.chat_message(msg["role"]):
            if msg.get("citations") and isinstance(msg["content"], list):
                st.markdown(f"**{CITATIONS_TITLE}**")
                for i, cite in enumerate(msg["content"], 1):
                    st.markdown(
                        CITATION_LINE.format(
                            i=i,
                            file=cite.get("file_name", ""),
                            page=cite.get("page", "?"),
                            snippet=str(cite.get("snippet", ""))[:200],
                        )
                    )
            else:
                st.markdown(msg["content"])

    if st.session_state.get("offer_web_search"):
        st.caption(WEB_SEARCH_HINT)
        pending = st.session_state.get("pending_web_search_query") or ""
        if st.button(BTN_WEB_SEARCH, key="btn_web_search"):
            _submit_chat(
                token,
                "",
                confirm_web_search=True,
                web_search_query=pending or None,
            )

    suggestions = st.session_state.get("suggested_questions") or []
    if suggestions:
        st.caption(SUGGESTED_QUESTIONS_TITLE)
        for i, q in enumerate(suggestions):
            if st.button(q, key=f"suggest_{i}_{hash(q) & 0xFFFF}"):
                _submit_chat(token, q)

    if prompt := st.chat_input(CHAT_INPUT_PLACEHOLDER):
        st.session_state["offer_web_search"] = False
        _submit_chat(token, prompt)
