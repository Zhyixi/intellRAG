# outside module
import sys, os
sys.path.extend(['.', '..'])
from  .llms.huggingface import HuggingFaceLLM
from  .llms.openai import OpenAI
from  .embeddings.huggingface import HuggingFaceEmbedding
from  .postprocessor.flag_embedding_reranker import FlagEmbeddingReranker
from transformers import BitsAndBytesConfig
from  .core import PromptTemplate
import torch, requests
import logging
# internal module
from configs.config import OLLAMA_PORT, CHAT_OLLAMA_IP, api_key, RAG_OLLAMA_IP
from  .llms.ollama import Ollama
from  .embeddings.ollama import OllamaEmbedding
os.environ["CUDA_VISIBLE_DEVICES"] = ""

# 定義 messages_to_prompt 函數
def messages_to_prompt(messages):
  prompt = ""
  for message in messages:
    if message.role == 'system':
      prompt += f"\n{message.content}</s>\n"
    elif message.role == 'user':
      prompt += f"\n{message.content}</s>\n"
    elif message.role == 'assistant':
      prompt += f"\n{message.content}</s>\n"
  # ensure we start with a system prompt, insert blank if needed
  if not prompt.startswith("\n"):
    prompt = "\n</s>\n" + prompt
  # add final assistant prompt
  prompt = prompt + "\n"
  return prompt
# 定義 quantization_config 變量
quantization_config = BitsAndBytesConfig(
    load_in_4bit=True,
    bnb_4bit_compute_dtype=torch.float16,
    bnb_4bit_quant_type="nf4",
    bnb_4bit_use_double_quant=True,
)

def create_model(cloud=False, **para):
  required_params = {'temperature', 'top_k', 'top_p', 'do_sample', 'model_name'}
  for param in required_params:
      if param not in para:
          raise ValueError(f"Missing required parameter: {param}")
  if cloud:
    logging.info("Initializing cloud model...")
    global_llm = OpenAI(
      model="gpt-4o-mini",
      api_key=api_key
    )
  else:
    try:
      global_llm = Ollama(base_url=f'{CHAT_OLLAMA_IP}:{OLLAMA_PORT}',
                          model=para["model_name"], request_timeout=120.0,
                          json_mode=False, prompt_key="你好阿",
                          additional_kwargs={"top_k": 50,"top_p": 0.9})
    except Exception as e:
      logging.info(f"{e}")
      logging.info("Initializing local model...")
      global_llm = HuggingFaceLLM(
          model_name=para["model_name"],
          tokenizer_name=para["model_name"],
          query_wrapper_prompt=PromptTemplate("\n</s>\n\n{query_str}</s>\n\n"),
          context_window=3900,
          max_new_tokens=256,
          model_kwargs={"quantization_config": quantization_config},
          tokenizer_kwargs={"use_fast": True},  # 在這裡設置 use_fast=True
          generate_kwargs={"temperature":para["temperature"],"top_k":para["top_k"],"top_p":para["top_p"],"do_sample":para["do_sample"]},
          messages_to_prompt=messages_to_prompt,
          device_map="auto")
  return global_llm

def create_embedding(cloud=False, **para):
  # 建立 base_url
  base_url = f"{RAG_OLLAMA_IP}:{OLLAMA_PORT}"
  required_params = {'model_name'}
  for param in required_params:
      if param not in para:
          raise ValueError(f"Missing required parameter: {param}")
  logging.info("Initializing embedding model...")
  model_name = para['model_name']
  try:
      # 嘗試發送健康檢查請求或嵌入測試請求
      response = requests.post(
          f"{base_url}/api/embed",
          json={"model": model_name, "text": "測試連線"},
          timeout=5
      )
      if response.status_code == 200:
          print(f"✅ 成功連線到 OllamaEmbedding 服務: {base_url}")
      else:
          print(f"❌ 連線失敗: 狀態碼 {response.status_code}，回應內容: {response.text} \n{base_url}")
  except requests.RequestException as e:
      print(f"❌ 連線失敗: 狀態碼 {e}，\n{base_url}")
  try:
    embed_model = OllamaEmbedding(base_url=f'{RAG_OLLAMA_IP}:{OLLAMA_PORT}',model_name=para['model_name'], request_timeout=60.0)
    pass
  except Exception as e:
    logging.info(f"{e}")
    embed_model = HuggingFaceEmbedding(model_name=para['model_name'], device='cpu')
  return embed_model

def create_embedding_reranker(**para):
   # Ensure fp16 is not used on CPU
  rag_reranker = FlagEmbeddingReranker(top_n=para['top_n'],
                                       model=para['model'],
                                       use_fp16=False)
  return rag_reranker