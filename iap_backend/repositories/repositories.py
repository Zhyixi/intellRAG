# coding: utf-8
"""
Repositories module.
Created on Wed Apr 19 16:13:14 2023

@author: Sean Chang
"""
import sys
sys.path.extend(['.', '..'])
from contextlib import AbstractContextManager
from typing import Callable, Iterator
from sqlalchemy import text
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session
from sqlalchemy import MetaData, Table, text, select
from sqlalchemy import Column, DateTime, Float, Integer, String, Table, MetaData, delete, text
from sqlalchemy.exc import SQLAlchemyError
from elasticsearch import Elasticsearch, AsyncElasticsearch
import logging
import os
from typing import List, Dict
from pymongo import MongoClient
import redis
import pandas as pd
import json
import pickle


class MongoRepository:
    def __init__(self, default_database:str, mongo_client:MongoClient):
        """
        初始化 MongoDB 客戶端
        """
        self.client = mongo_client
        self.default_database = default_database

    def get_collection(self, collection_name: str, database_name: str=None):
        """
        動態選擇資料庫和集合
        """
        database_name = database_name or self.default_database  # 使用默認資料庫
        database = self.client[database_name]
        collection = database[collection_name]
        return collection

    def find_list(self, collection_name: str, query: Dict, database_name: str = None):
        """
        查詢集合中的數據，返回列表
        """
        collection = self.get_collection(collection_name, database_name)
        return list(collection.find(query))

    def find_df(self, collection_name: str, query: Dict, database_name: str = None, sort: list = None, limit: int = 0):
        """
        查詢集合中的數據，返回 DataFrame，支援排序與限制筆數
        """
        collection = self.get_collection(collection_name, database_name)
        
        # 1. 建立查詢游標
        cursor = collection.find(query)
        
        # 2. 若有傳入排序條件則套用
        if sort:
            cursor = cursor.sort(sort)
            
        # 3. 若有傳入限制筆數則套用
        if limit > 0:
            cursor = cursor.limit(limit)
            
        # 4. 轉換為 DataFrame
        result = list(cursor)
        df = pd.DataFrame(result)
        return df

    def insert_one(self, collection_name: str, document: Dict, database_name: str = None):
        """
        向集合中插入單條數據
        """
        collection = self.get_collection(collection_name, database_name)
        return collection.insert_one(document)

    def insert_many(self, collection_name: str, data: List[Dict], database_name: str = None):
        """
        向集合中插入多條數據
        """
        collection = self.get_collection(collection_name, database_name)
        return collection.insert_many(data)

    def insert_df(self, collection_name: str, df: pd.DataFrame, database_name: str = None):
        """
        插入 DataFrame 到集合
        """
        try:
            data = df.to_dict('records')  # 將 DataFrame 轉為字典列表
            self.insert_many(collection_name, data, database_name)
            return True
        except Exception as e:
            print(f"插入 DataFrame 時發生錯誤: {e}")
            return False

    def delete_many(self, collection_name: str, query: Dict, database_name: str = None):
        """
        刪除集合中的多條數據
        """
        collection = self.get_collection(collection_name, database_name)
        return collection.delete_many(query)

    def update_one(
        self,
        collection_name: str,
        query: Dict,
        update: Dict,
        database_name: str = None,
        upsert: bool = False,
    ):
        collection = self.get_collection(collection_name, database_name)
        return collection.update_one(query, update, upsert=upsert)

    def find_one(self, collection_name: str, query: Dict, database_name: str = None):
        collection = self.get_collection(collection_name, database_name)
        return collection.find_one(query)

    

    # 1. 移除 async，回歸同步實作 (配合你的 MongoClient)
    def aggregate_to_df(self, collection_name: str, pipeline: list, database_name: str = None) -> pd.DataFrame:
        """
        執行 Aggregate 運算並將結果轉為 DataFrame (同步版)
        """
        try:
            # 2. 使用你自定義的助手方法，確保拿到的是「集合」物件
            collection = self.get_collection(collection_name, database_name)
            
            # 3. 執行聚合查詢 (同步操作)
            cursor = collection.aggregate(pipeline)
            
            # 4. 直接轉為 list (同步環境下 list(cursor) 是最快的方式)
            results = list(cursor)
            
            # 5. 轉換成 DataFrame
            if not results:
                return pd.DataFrame()
                
            return pd.DataFrame(results)
            
        except Exception as e:
            logging.error(f"MongoDB Aggregate Error: {str(e)}")
            raise e
    
class ElasticsearchManagerRepository:
    def __init__(self, es_client: AsyncElasticsearch):
        self.es = es_client

    async def close(self):
        await self.es.close()
    
    async def conn_healthy(self):
        is_connected = await self.es.ping()
        if is_connected:
            return True
        else:
            return False
            
    async def index_exists(self, index_name:str) -> bool:
        exists = await self.es.indices.exists(index=index_name)
        logging.info(f"Index '{index_name}' exists: {exists}")
        return exists

    async def delete_index(self, index_name:str) -> bool:
        if await self.index_exists(index_name):
            await self.es.indices.delete(index=index_name)
            logging.info(f"Deleted index: {index_name}")
            return True
        return False

    async def create_index(self, index_name:str, mapping: dict, settings: dict = None) -> None:
        body = {"mappings": mapping}
        if settings:
            body["settings"] = settings
        await self.es.indices.create(index=index_name, body=body)
        logging.info(f"Created index: {index_name}")

    async def update_chunk(self, content, doc_id, index_name):
        pass
        self.es.update(
            index=index_name,
            id=doc_id,
            body={
                "doc": {
                    "content": content
                }
            }
        )
        pass

    async def test_analyzer(self, analyzer: str, text: str) -> dict:
        return await self.es.indices.analyze(index=self.index_name, analyzer=analyzer, text=text)

class TableManagerRepository:
    def __init__(self, session_factory, engine, inspector, base):
        self._session_factory = session_factory
        self._engine = engine
        self._inspector = inspector
        self._base = base

    def drop_table(self, table_name: str):
        with self._session_factory() as session:
            try:
                session.execute(text(f"DROP TABLE IF EXISTS `{table_name}`"))
                session.commit()
            except SQLAlchemyError as e:
                session.rollback()
                raise RuntimeError(f"Failed to drop table {table_name}: {e}")
    
    def query_table(self, table_name: str) -> pd.DataFrame:
        metadata = MetaData()
        try:
            table = Table(table_name, metadata, autoload_with=self._engine)
            with self._session_factory() as session:
                try:
                    stmt = select(table)
                    result = session.execute(stmt)
                    rows = result.mappings().all()  # ✅ 把 Row 轉成 dict
                    df = pd.DataFrame(rows)
                    return df
                except SQLAlchemyError as e:
                    session.rollback()
                    raise RuntimeError(f"Failed to query table '{table_name}': {e}")
        except Exception as e:
            return pd.DataFrame()
    


    def delete_all(self, table_name: str):
        with self._session_factory() as session:
            try:
                table = Table(table_name, self._base.metadata, autoload_with=self._engine, extend_existing=True)
                delete_stmt = delete(table)
                session.execute(delete_stmt)
                session.commit()
            except Exception as e:
                session.rollback()
                raise RuntimeError(f"Failed to delete records in table '{table_name}': {e}")

    def _infer_sqlalchemy_type(self, pd_dtype):
        if pd_dtype == 'int64':
            return Integer
        elif pd_dtype == 'float64':
            return Float
        elif pd_dtype == 'object':
            return String(255)
        elif pd_dtype == 'str':
            return String(255)
        elif pd_dtype == 'datetime64[ns]':
            return DateTime
        else:
            raise ValueError(f"Unknown pandas dtype: {pd_dtype}")

    def create_table_if_not_exists(self, df: pd.DataFrame, table_name: str):
        tables = self._inspector.get_table_names()
        if table_name not in tables:
            attrs = {
                '__tablename__': table_name,
                '__table_args__': {'extend_existing': True},
                'id': Column(Integer, primary_key=True, autoincrement=True)
            }
            for column_name, dtype in df.dtypes.items():
                if column_name == 'id':
                    continue
                sqlalchemy_type = self._infer_sqlalchemy_type(dtype.name)
                attrs[column_name] = Column(sqlalchemy_type)
            Model = type(table_name, (self._base,), attrs)
            self._base.metadata.create_all(self._engine)
        else:
            metadata = MetaData()
            table = Table(table_name, metadata, autoload_with=self._engine)

            class ExistingTable(self._base):
                __table__ = table
            return ExistingTable
        return Model

    def replace_nan_with_none(self, df: pd.DataFrame) -> pd.DataFrame:
        df = df.copy()
        df.reset_index(drop=True, inplace=True)
        for column in df.columns:
            df[column] = df[column].apply(lambda x: None if pd.isna(x) else x)
        return df

    def insert_dataframe(self, df: pd.DataFrame, table_name: str):
        try:
            # 將所有 datetime64[us] 欄位轉換為支援度較高的 datetime64[ns]
            for col in df.columns:
                if 'datetime64' in str(df[col].dtype):
                    df[col] = pd.to_datetime(df[col]).dt.to_pydatetime()
            with self._session_factory() as session:
                Model = self.create_table_if_not_exists(df, table_name)
                df = self.replace_nan_with_none(df)
                df_dict = df.to_dict(orient="records")
                session.bulk_insert_mappings(Model, df_dict)
                session.commit()
            return True
        except Exception as e:
            logging.error(f"{e}")
            raise AssertionError("e")
        

    def execute_raw_sql(self, sql_query: str, params: dict | None = None):
        with self._session_factory() as session:
            try:
                result = session.execute(text(sql_query), params or {})
                if result.returns_rows:
                    columns = result.keys()
                    df = pd.DataFrame(result.fetchall(), columns=columns)
                    return df
                else:
                    session.commit()
                    return result.rowcount
            except Exception as e:
                session.rollback()
                raise RuntimeError(f"Failed to execute raw SQL: {e}")


class RedisRepository:
    def __init__(self, redis_client: redis.Redis, default_namespace: str = "default"):
        """
        初始化 Redis 客戶端與預設命名空間
        """
        self.client = redis_client
        self.default_namespace = default_namespace

    def set_list(self,my_list:list, key_name="default_list"):
        self.client.set(key_name, pickle.dumps(my_list))

    def get_list(self, key_name="default_list"):
        restored = pickle.loads(self.client.get(key_name))
        return restored
    
    def append_list(self, item, key_name="default_list"):
        current = self.get_list(key_name=key_name)
        current.append(f"{item}")
        self.set_list(current)