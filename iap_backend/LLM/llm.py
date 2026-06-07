# # outside module
# import sys, os
# sys.path.extend(['.', '..'])
# from LLM.model_factory import create_model, create_embedding, create_embedding_reranker
# from configs.config import chat_model_enable, pd_instr_model_enable, rag_model_enable, rag_model_cloud, pd_instr_model_cloud, chat_model_cloud
# from  .core import Settings
# from configs.config import reranker_top_k, rerank_model_name, rag_model_name, rag_model_cloud
# from transformers import AutoTokenizer
# chat_model = None
# if chat_model_enable:
#     from configs.config import chat_model_name, chat_model_temperature, chat_model_top_k, chat_model_top_p, chat_model_do_sample
    
#     chat_model = create_model(cloud=chat_model_cloud,
#                               model_name=chat_model_name,
#                           temperature= chat_model_temperature,
#                           top_k= chat_model_top_k,
#                           top_p= chat_model_top_p,
#                           do_sample=chat_model_do_sample, model_source='ollama') # 
#     Settings.llm = chat_model
    
# pd_instr_model = None
# if pd_instr_model_enable:
#     from configs.config import pd_instr_model_name, pd_instr_model_temperature, pd_instr_model_top_k, pd_instr_model_top_p, pd_instr_model_do_sample 
#     if chat_model_enable and chat_model_name == pd_instr_model_name:
#       pd_instr_model = chat_model
#     else:
#       pd_instr_model = create_model(cloud=pd_instr_model_cloud,model_name=pd_instr_model_name,
#                             temperature= pd_instr_model_temperature,
#                             top_k= pd_instr_model_top_k,
#                             top_p= pd_instr_model_top_p,
#                             do_sample=pd_instr_model_do_sample)
# rag_embedding_model = None
# rag_reranker = None
# if rag_model_enable:
#     rag_embedding_model = create_embedding(cloud=rag_model_cloud, model_name=rag_model_name)
#     rag_reranker = create_embedding_reranker(top_n=reranker_top_k, model=rerank_model_name)
#     Settings.embed_model = rag_embedding_model
#     # tokenizer = AutoTokenizer.from_pretrained(rag_model_name)

# """
# temperature: 控制生成文本的隨機性。較低的值(如0.1)會使模型,更傾向於生成概率較高的詞彙。較高的值(如1.0或更高)則會使生成的文本更加多樣化。
# top_k: 這個參數設定在每一步生成時，模型會從機率最高的前K個詞中進行選擇。設定為50意味著模型只會考慮機率最高的前50個詞，這樣可以避免選擇一些機率較低且可能不相關的詞彙。
# top_p: kernel sampling。模型會選擇機率累積達到top_p百分比的詞彙集合中的詞。例如，設定為0.95時，模型會在使機率總和達到95%的詞彙中進行選擇，
# 這通常比top_k更靈活，因為top_p會動態調整考慮的詞彙數量。
# do_sample: 當這個參數設定為True時，模型在生成每個詞時會進行隨機選擇（基於前面提到的top_k和top_p的約束）。
# 如果設定為False，模型會選擇機率最高的詞，這通常會生成更加一貫但也較為單一的結果。
# """



# def embed_text(text: str):
#     from transformers import AutoTokenizer, AutoModel
#     import torch
#     tokenizer = AutoTokenizer.from_pretrained("sentence-transformers/all-MiniLM-L6-v2", clean_up_tokenization_spaces=True)
#     model = AutoModel.from_pretrained("sentence-transformers/all-MiniLM-L6-v2")
#     # 分词并生成输入张量
#     inputs = tokenizer(text, return_tensors="pt", truncation=True, padding=True)
#     with torch.no_grad():
#         # 生成文本嵌入
#         outputs = model(**inputs)
#     # 平均池化最后一层的隐藏状态作为文本的表示
#     return outputs.last_hidden_state.mean(dim=1).squeeze()