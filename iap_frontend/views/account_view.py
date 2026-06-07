"""個人管理 — 資料 / API Key / Langfuse 用量。"""
from __future__ import annotations

import pandas as pd
import streamlit as st

from HandleRequest.account_client import (
    delete_api_key,
    get_api_key_status,
    get_usage_by_model,
    get_usage_daily,
    get_usage_summary,
    set_api_key,
    update_profile,
)
from HandleRequest.auth_client import get_me
from views.strings import (
    ACCOUNT_TITLE,
    API_KEY_CONFIGURED,
    API_KEY_INFO,
    API_KEY_LAST_UPDATED,
    API_KEY_NOT_CONFIGURED,
    API_KEY_STATUS,
    BTN_CLEAR_KEY,
    BTN_LOGOUT,
    BTN_SAVE_KEY,
    BTN_SAVE_NAME,
    BTN_UPDATE_PASSWORD,
    CHART_COST_TREND,
    CHART_TOKEN_TREND,
    ERR_KEY_SAVE_FAILED,
    ERR_KEY_TOO_SHORT,
    ERR_LOAD_PROFILE,
    ERR_UPDATE_FAILED,
    EXPANDER_PASSWORD,
    LABEL_CURRENT_PASSWORD,
    LABEL_EMAIL_READONLY,
    LABEL_NEW_PASSWORD,
    LABEL_NICKNAME,
    LABEL_OPENAI_KEY,
    METRIC_MONTH_COST,
    METRIC_MONTH_TOKENS,
    METRIC_TODAY_COST,
    METRIC_TODAY_TOKENS,
    PLEASE_LOGIN,
    SUCCESS_KEY_CLEARED,
    SUCCESS_KEY_SAVED,
    SUCCESS_PASSWORD_UPDATED,
    SUCCESS_UPDATED,
    TAB_API_KEY,
    TAB_PROFILE,
    TAB_USAGE,
    USAGE_BY_MODEL,
    USAGE_LANGFUSE_DISABLED,
)


def render_account_page(cookies):
    token = st.session_state.get("auth_token") or cookies.get("auth_token")
    if not token:
        st.warning(PLEASE_LOGIN)
        return

    st.title(ACCOUNT_TITLE)
    tab_profile, tab_apikey, tab_usage = st.tabs([TAB_PROFILE, TAB_API_KEY, TAB_USAGE])

    with tab_profile:
        me = get_me(token)
        if me["status_code"] != 200:
            st.error(ERR_LOAD_PROFILE)
        else:
            user = me["data"]
            st.write(f"**{LABEL_EMAIL_READONLY}：** {user.get('email', '')}")
            new_name = st.text_input(LABEL_NICKNAME, value=user.get("display_name", ""))
            if st.button(BTN_SAVE_NAME):
                r = update_profile(token, display_name=new_name)
                if r["status_code"] == 200:
                    cookies["username"] = new_name
                    cookies.save()
                    st.success(SUCCESS_UPDATED)
                else:
                    st.error(ERR_UPDATE_FAILED)
            with st.expander(EXPANDER_PASSWORD):
                cur = st.text_input(LABEL_CURRENT_PASSWORD, type="password", key="cur_pw")
                new_pw = st.text_input(LABEL_NEW_PASSWORD, type="password", key="new_pw")
                if st.button(BTN_UPDATE_PASSWORD):
                    r = update_profile(token, password=new_pw, current_password=cur)
                    if r["status_code"] == 200:
                        st.success(SUCCESS_PASSWORD_UPDATED)
                    else:
                        st.error((r.get("data") or {}).get("detail", ERR_UPDATE_FAILED))
        if st.button(BTN_LOGOUT):
            cookies["logged_in"] = "False"
            cookies["auth_token"] = ""
            cookies.save()
            st.session_state.clear()
            st.rerun()

    with tab_apikey:
        status = get_api_key_status(token)
        configured = (status.get("data") or {}).get("configured", False)
        status_text = API_KEY_CONFIGURED if configured else API_KEY_NOT_CONFIGURED
        st.write(API_KEY_STATUS, status_text)
        if configured and status.get("data", {}).get("updated_at"):
            st.caption(API_KEY_LAST_UPDATED.format(time=status["data"]["updated_at"]))
        key_input = st.text_input(LABEL_OPENAI_KEY, type="password", placeholder="sk-…")
        c1, c2 = st.columns(2)
        with c1:
            if st.button(BTN_SAVE_KEY):
                if len(key_input) < 10:
                    st.error(ERR_KEY_TOO_SHORT)
                else:
                    r = set_api_key(token, key_input)
                    if r["status_code"] == 200:
                        st.success(SUCCESS_KEY_SAVED)
                    else:
                        st.error(ERR_KEY_SAVE_FAILED)
        with c2:
            if st.button(BTN_CLEAR_KEY) and configured:
                delete_api_key(token)
                st.success(SUCCESS_KEY_CLEARED)
                st.rerun()
        st.info(API_KEY_INFO)

    with tab_usage:
        summary_resp = get_usage_summary(token)
        summary = summary_resp.get("data") or {}
        if not summary.get("langfuse_enabled"):
            st.warning(summary.get("note", USAGE_LANGFUSE_DISABLED))
        else:
            st.caption(summary.get("note", ""))
            today = summary.get("today") or {}
            month = summary.get("month") or {}
            c1, c2, c3, c4 = st.columns(4)
            c1.metric(METRIC_TODAY_TOKENS, f"{today.get('tokens', 0):,}")
            c2.metric(METRIC_TODAY_COST, f"${today.get('cost_usd', 0):.4f}")
            c3.metric(METRIC_MONTH_TOKENS, f"{month.get('tokens', 0):,}")
            c4.metric(METRIC_MONTH_COST, f"${month.get('cost_usd', 0):.4f}")

            daily_resp = get_usage_daily(token, days=30)
            series = (daily_resp.get("data") or {}).get("series") or []
            if series:
                df = pd.DataFrame(series)
                if "date" in df.columns:
                    df = df.set_index("date")
                    st.subheader(CHART_TOKEN_TREND)
                    if "tokens" in df.columns:
                        st.line_chart(df["tokens"])
                    st.subheader(CHART_COST_TREND)
                    if "cost_usd" in df.columns:
                        st.line_chart(df["cost_usd"])

            model_resp = get_usage_by_model(token)
            models = (model_resp.get("data") or {}).get("models") or []
            if models:
                st.subheader(USAGE_BY_MODEL)
                st.dataframe(pd.DataFrame(models), use_container_width=True)
