"""IntelliAgnet — Streamlit 入口。"""
from __future__ import annotations

import os
import sys

import streamlit as st

sys.path.extend([".", ".."])

from streamlit_cookies_manager import EncryptedCookieManager
from views.account_view import render_account_page
from views.auth_view import render_auth_screen
from views.documents_view import render_documents_page
from views.notebook_view import render_notebook_page
from views.strings import APP_TITLE, INIT_LOADING, NAV_ACCOUNT, NAV_DOCUMENTS, NAV_NOTEBOOK

st.set_page_config(page_title=APP_TITLE, layout="wide", initial_sidebar_state="expanded")

cookies = EncryptedCookieManager(prefix="iap_nb_", password=os.getenv("COOKIE_SECRET", "iap-notebook-secret-key"))
if not cookies.ready():
    st.warning(INIT_LOADING)
    st.stop()

if "logged_in" not in cookies:
    cookies["logged_in"] = "False"

if cookies.get("logged_in") == "True" and cookies.get("auth_token"):
    st.session_state.setdefault("auth_token", cookies.get("auth_token"))
    st.session_state.setdefault("logged_in", True)

_cookie_dirty = False
if os.getenv("ENV", "dev") == "dev":
    _e2e_token = st.query_params.get("e2e_token")
    if _e2e_token:
        st.session_state["auth_token"] = _e2e_token
        st.session_state["logged_in"] = True
        cookies["auth_token"] = _e2e_token
        cookies["logged_in"] = "True"
        _cookie_dirty = True

if _cookie_dirty:
    cookies.save()

_logged_in = bool(st.session_state.get("logged_in")) or (
    cookies.get("logged_in") == "True" and cookies.get("auth_token")
)
if _logged_in and (st.session_state.get("auth_token") or cookies.get("auth_token")):
    st.session_state.setdefault("auth_token", st.session_state.get("auth_token") or cookies.get("auth_token"))
    st.session_state.setdefault("logged_in", True)

    def _page_notebook():
        render_notebook_page(cookies)

    def _page_documents():
        render_documents_page(cookies)

    def _page_account():
        render_account_page(cookies)

    pages = [
        st.Page(_page_notebook, title=NAV_NOTEBOOK, icon="📓"),
        st.Page(_page_documents, title=NAV_DOCUMENTS, icon="📄"),
        st.Page(_page_account, title=NAV_ACCOUNT, icon="👤"),
    ]
    pg = st.navigation(pages)
    pg.run()
else:
    render_auth_screen(cookies)
