# import os, sys
# sys.path.extend([".", ".."])
# import pandas as pd
# import streamlit as st
# from common.utils import make_arrow_compatible
# from HandleRequest.chat import pe
# import datetime
# @st.cache_resource(show_spinner=False)
# def load_data(start_date:str, end_date:str):
#     with st.spinner(text="數據載入中,請等待1至2分鐘\nLoading and indexing the data - hang tight! This should take 1-2 minutes."):
#         # if 0:
#         #     source_df_response = set_query_data(start_date, end_date)
#         #     if source_df_response['status_code'] != 200:
#         #         raise AssertionError("初始數據不存在")
#         cache_data = get_cache_data()
#         df = pd.DataFrame(cache_data['res']['data']) # 
#         df = make_arrow_compatible(df)
#         return df