import os
import streamlit as st
from streamlit_feedback import streamlit_feedback
import requests
from datetime import datetime
import json
import yaml
from configs.config import common_url
def handle_feedback():
    """Handle feedback form submission."""
    def fbcb():
        message_id = len(st.session_state.messages) - 1
        if message_id >= 0:
            score_mappings = {
                "thumbs": {
                    "👍": 1,
                    "👎": -1
                }
            }

            scores = score_mappings["thumbs"]
            score = scores.get(st.session_state.fb_k["score"])
            # feedback_type_str = f"{"thumbs"} {st.session_state.fb_k['score']}"
            current_datetime = datetime.now()
            current_date_str = current_datetime.strftime("%Y-%m-%d")
            feedback_data = {
                "question": st.session_state.messages[message_id-1]["content"],
                "answer": st.session_state.messages[message_id]["content"],
                "feedback": st.session_state.fb_k,
                'bot_msg': st.session_state.messages[message_id]["content"],
                'usr_msg': st.session_state.messages[message_id-1]["content"],
                'feedback': score,
                'suggestion': st.session_state.fb_k["text"],
                'date_time': current_date_str,
                'usr_id': 'user_id'
            }
            st.session_state.messages[message_id]["feedback"] = feedback_data
            url = common_url.feedback
            input_para = {
                    'bot_msg': st.session_state.messages[message_id]["content"],
                    'usr_msg': st.session_state.messages[message_id-1]["content"],
                    'feedback': score,
                    'suggestion': st.session_state.fb_k["text"],
                    'date_time': current_date_str,
                    'usr_id': 'user_id'}
            print(input_para)
            x = requests.post(url, data = json.dumps(input_para))
            print(x.text)


    streamlit_feedback(feedback_type="thumbs", optional_text_label="[Optional]", align="flex-start", key='fb_k')
    st.form_submit_button('Save feedback', on_click=fbcb)
