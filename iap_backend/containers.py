"""Containers module."""
import os
import sys
sys.path.extend(['.','..'])
from services.services import MongoService, TranslatorService
from services.rag_service import RAGService, RetriveService
from dependency_injector import containers, providers
from database.database import Database
from repositories.repositories import ElasticsearchManagerRepository, TableManagerRepository, MongoRepository, RedisRepository
from configs.config import (
    ENV, REDIS_IP, MONGO_URI, MYSQL_URL, ES_IP, ES_PORT,
    api_key, chat_model_cloud, chat_model_name, rerank_model_name,
)
import redis
from elasticsearch import Elasticsearch, AsyncElasticsearch
from pymongo import MongoClient
import requests, logging
from typing import List
import numpy as np
from sklearn.preprocessing import normalize
from langchain_openai import ChatOpenAI
from langchain_ollama import ChatOllama
from common.embedding_resolver import apply_embedding_mode_to_config, create_embedding_model
from langfuse import Langfuse
from common.langfuse_tracing import langfuse_configured, langfuse_environment
from langfuse.langchain import CallbackHandler


_container_instance: "Container | None" = None


def get_container() -> "Container":
    """Return the process-wide DI container (single MongoClient / Redis / ES pool)."""
    global _container_instance
    if _container_instance is None:
        _container_instance = Container()
    return _container_instance


def shutdown_container() -> None:
    """Release long-lived clients opened by the DI container."""
    global _container_instance
    if _container_instance is None:
        return
    c = _container_instance
    for name, closer in (
        ("mongo_db", lambda client: client.close()),
        ("redis_client", lambda client: client.close()),
        ("es_client", lambda client: client.close()),
    ):
        try:
            provider = getattr(c, name, None)
            if provider is not None:
                closer(provider())
        except Exception as exc:
            logging.warning("Container shutdown %s failed: %s", name, exc)
    _container_instance = None


def _create_langfuse_client():
    if not langfuse_configured():
        logging.info(
            "Langfuse disabled: set LANGFUSE_PUBLIC_KEY and LANGFUSE_SECRET_KEY to enable tracing/prompts"
        )
        return None
    return Langfuse(environment=langfuse_environment())


def _create_langfuse_handler():
    if not langfuse_configured():
        return None
    return CallbackHandler()


class Container(containers.DeclarativeContainer): # 定義宣告式容器
    # 註冊需要注入依賴的模組
    wiring_config = containers.WiringConfiguration(modules=[
        "fast_api_service.api.pe.pe",
        "fast_api_service.api.ae.ae",
        "fast_api_service.api.auth.auth",
        "fast_api_service.api.notebook.notebook",
        "fast_api_service.api.notebook.documents",
        "fast_api_service.api.account.account",
    ])
    
    langfuse_client = providers.Singleton(_create_langfuse_client)
    langfuse_handler = providers.Factory(_create_langfuse_handler)

    _openai_api_key = api_key or os.getenv("OPENAI_API_KEY", "")
    _openai_llm_kwargs = dict(
        model=chat_model_name,
        openai_api_key=_openai_api_key,
        temperature=0,
        max_tokens=8192,
        timeout=300,
        streaming=False,
        top_p=1.0,
    )

    if chat_model_cloud:
        _openai_llm_kwargs["openai_api_base"] = "https://api.openai.com/v1"
        ollama_general_llm = providers.Singleton(ChatOpenAI, **_openai_llm_kwargs)
        vllm_general_llm = providers.Singleton(ChatOpenAI, **_openai_llm_kwargs)
    elif ENV == "prod":
        ollama_general_llm = providers.Singleton(
            ChatOpenAI,
            openai_api_base='http://10.110.209.10:4000/v1',
            model="faca-model-prod",
            openai_api_key="sk-any-key",
            temperature=0,
            max_tokens=8192,
            timeout=300,
            streaming=False,
            top_p=1.0,
        )
        vllm_general_llm = ollama_general_llm
    else:
        ollama_general_llm = providers.Singleton(
            ChatOllama,
            base_url='http://10.110.209.10:11435',
            model="qwen3:8b",
            temperature=0,
            top_p=1.0,
            request_timeout=180,
        )
        vllm_general_llm = providers.Singleton(
            ChatOpenAI,
            base_url='http://10.110.209.10:4000/v1',
            model_name="faca-model-dev",
            api_key="vllm-no-key",
            temperature=0.0,
            max_tokens=8192,
            timeout=300,
            streaming=False,
            top_p=1.0,
            presence_penalty=0.0,
            frequency_penalty=0.0,
            model_kwargs={"seed": 42},
            include_response_headers=True,
        )
    translator = providers.Singleton(TranslatorService, llm=vllm_general_llm)
    
    #%% embedding model（啟動時依 GPU/RAM 或設定選 local / cloud）
    _embedding_mode = apply_embedding_mode_to_config()
    logging.info("Embedding mode at startup: %s", _embedding_mode)
    embedding_model = providers.Singleton(create_embedding_model)
    try:
        _probe = embedding_model().embed_query("healthcheck")
        if _probe:
            logging.info("Embedding model ok (dim=%s)", len(_probe))
    except Exception as exc:
        logging.error("Embedding model healthcheck failed: %s", exc)
    
    #%% Redis client
    redis_client = providers.Singleton(
        redis.Redis,
        host=REDIS_IP,
        port=6379,
        db=0,
        password='123456'
    )
    redis_repository = providers.Factory(
        RedisRepository,redis_client=redis_client)
    # redis_instance = redis_client()
    # redis_instance.ping()
    
    #%% Mongo
    mongo_db = providers.Singleton(MongoClient, MONGO_URI)
    mongo_repository = providers.Factory(
        MongoRepository,
        default_database="LLM",
        mongo_client=mongo_db
    )
    mongo_service = providers.Factory(
        MongoService,
        mongo_repo=mongo_repository,
    )
    from langgraph.checkpoint.mongodb import MongoDBSaver

    mongo_checkpointer = providers.Singleton(MongoDBSaver, client=mongo_db)
    # mysql
    sql_db = providers.Singleton(Database, db_url=MYSQL_URL)
    
    table_manager_repository = providers.Factory(
    TableManagerRepository,
    session_factory=sql_db.provided.session,
    engine=sql_db.provided._engine,
    inspector=sql_db.provided._inspector,
    base=sql_db.provided._base)
    # iap mysql
    iap_db = providers.Singleton(Database, db_url="mysql+pymysql://AlvinYC:User%40Compal%21@10.129.137.138:3306/faca")
    iap_table_manager_repository = providers.Factory(
    TableManagerRepository,
    session_factory=iap_db.provided.session,
    engine=iap_db.provided._engine,
    inspector=iap_db.provided._inspector,
    base=iap_db.provided._base)
    
    # Elasticsearch
    es_client = providers.Singleton(Elasticsearch,
                                    hosts=[f"http://{ES_IP}:{ES_PORT}"],
                                    basic_auth=("elastic", "123456789"))
    
    client = es_client()
    try:
        # ping() 會回傳 True (成功) 或 False (失敗)
        if client.ping():
            print("✅ Elasticsearch 連線成功！")
        else:
            print("❌ Elasticsearch 連線失敗，請確認服務器狀態或網路設定。")
    except Exception as e:
        print(f"❌ 連線時發生錯誤: {e}")
    ###############
    es_manager_repository = providers.Singleton(
        ElasticsearchManagerRepository,
        es_client=es_client)
    
    reranker_model_path = providers.Object(
        None if chat_model_cloud or not rerank_model_name else rerank_model_name
    )
    
    rag_service = providers.Factory(RAGService,
                                    es_repo = es_manager_repository,
                                    embedding = embedding_model,
                                    tb_repo = table_manager_repository,
                                    ollama_general_llm = ollama_general_llm,
                                    vllm_general_llm = vllm_general_llm,
                                    translator=translator,
                                    langfuse_client = langfuse_client,
                                    langfuse_handler = langfuse_handler,
                                    reranker_model_path=reranker_model_path)
    
    translate_service = providers.Factory(TranslatorService, llm = vllm_general_llm)
    
    
    # Retrieve
    retrive_service = providers.Factory(RetriveService,
                                    es_repo = es_manager_repository,
                                    embedding = embedding_model,
                                    tb_repo = table_manager_repository,
                                    ollama_general_llm = ollama_general_llm,
                                    vllm_general_llm = vllm_general_llm,
                                    translator=translator,
                                    mongo_service=mongo_service,
                                    langfuse_client = langfuse_client,
                                    langfuse_handler = langfuse_handler,
                                    reranker_model_path=reranker_model_path)