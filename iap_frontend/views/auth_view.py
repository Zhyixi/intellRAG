"""登入 / 註冊介面。"""
from __future__ import annotations

import datetime

import streamlit as st

from HandleRequest.auth_client import login, register
from views.strings import (
    APP_TAGLINE,
    APP_TITLE,
    BTN_LOGIN,
    BTN_REGISTER,
    ERR_EMAIL_PASSWORD_REQUIRED,
    ERR_LOGIN_FAILED,
    ERR_PASSWORD_MISMATCH,
    ERR_PASSWORD_TOO_SHORT,
    ERR_REGISTER_FAILED,
    ERR_USER_INFO,
    LABEL_CONFIRM_PASSWORD,
    LABEL_DISPLAY_NAME,
    LABEL_EMAIL,
    LABEL_PASSWORD,
    LABEL_PASSWORD_MIN,
    SUCCESS_LOGIN,
    SUCCESS_REGISTER,
    TAB_LOGIN,
    TAB_REGISTER,
)


def _save_auth_cookies(cookies, *, token: str, user: dict):
    cookies["auth_token"] = token
    cookies["user_id"] = str(user.get("id", ""))
    cookies["email"] = user.get("email", "")
    cookies["username"] = user.get("display_name", user.get("email", ""))
    cookies["logintime"] = datetime.datetime.now().strftime("%Y-%m-%d %H:%M")
    cookies["logged_in"] = "True"
    cookies.save()


def render_auth_screen(cookies):
    st.title(APP_TITLE)
    st.caption(APP_TAGLINE)
    tab_login, tab_register = st.tabs([TAB_LOGIN, TAB_REGISTER])

    with tab_login:
        email = st.text_input(LABEL_EMAIL, key="login_email")
        password = st.text_input(LABEL_PASSWORD, type="password", key="login_password")
        if st.button(BTN_LOGIN, key="btn_login"):
            if not email or not password:
                st.error(ERR_EMAIL_PASSWORD_REQUIRED)
            else:
                resp = login(email, password)
                if resp["status_code"] == 200 and resp.get("data"):
                    token = resp["data"]["access_token"]
                    from HandleRequest.auth_client import get_me

                    me = get_me(token)
                    if me["status_code"] == 200:
                        _save_auth_cookies(cookies, token=token, user=me["data"])
                        st.session_state["auth_token"] = token
                        st.session_state["logged_in"] = True
                        st.success(SUCCESS_LOGIN)
                        st.rerun()
                    else:
                        st.error(ERR_USER_INFO)
                else:
                    detail = (resp.get("data") or {}).get("detail", ERR_LOGIN_FAILED)
                    st.error(str(detail))

    with tab_register:
        reg_email = st.text_input(LABEL_EMAIL, key="reg_email")
        reg_name = st.text_input(LABEL_DISPLAY_NAME, key="reg_name")
        reg_password = st.text_input(LABEL_PASSWORD_MIN, type="password", key="reg_password")
        reg_password2 = st.text_input(LABEL_CONFIRM_PASSWORD, type="password", key="reg_password2")
        if st.button(BTN_REGISTER, key="btn_register"):
            if reg_password != reg_password2:
                st.error(ERR_PASSWORD_MISMATCH)
            elif len(reg_password) < 8:
                st.error(ERR_PASSWORD_TOO_SHORT)
            else:
                resp = register(reg_email, reg_password, reg_name)
                if resp["status_code"] == 201 and resp.get("data"):
                    token = resp["data"]["access_token"]
                    from HandleRequest.auth_client import get_me

                    me = get_me(token)
                    if me["status_code"] == 200:
                        _save_auth_cookies(cookies, token=token, user=me["data"])
                        st.session_state["auth_token"] = token
                        st.session_state["logged_in"] = True
                        st.success(SUCCESS_REGISTER)
                        st.rerun()
                else:
                    detail = (resp.get("data") or {}).get("detail", ERR_REGISTER_FAILED)
                    st.error(str(detail))
