import os
import time
import openai
import pandas as pd
from ragas import evaluate
from datasets import Dataset
from ragas.testset.generator import TestsetGenerator
from langchain_openai import ChatOpenAI, OpenAIEmbeddings
from ragas.testset.evolutions import simple, reasoning, multi_context
from langchain_community.document_loaders.merge import MergedDataLoader
from langchain_community.document_loaders import DirectoryLoader, PyPDFLoader
from ragas.metrics import faithfulness, answer_relevancy, context_precision, context_recall
import asyncio

openai.api_key = "sk-proj-REDACTED"

async def main():
    # 指定資料夾路徑
    folder_path = "/app/RagasDataset2"
    # 使用 MergedDataLoader 合併 PyPDFLoader 讀取資料夾中的所有 PDF 檔案
    loaders = []
    for filename in os.listdir(folder_path):
        if filename.endswith(".pdf"):
            loader_pdf = PyPDFLoader(os.path.join(folder_path, filename))
            loaders.append(loader_pdf)
    # 合併數據集
    loader_all = MergedDataLoader(loaders=loaders)
    # 加載文件
    documents = loader_all.load()


    generator_llm = ChatOpenAI(model="gpt-4o-mini",openai_api_key=openai.api_key)
    # critic_llm = ChatOpenAI(model="gpt-4o-mini",openai_api_key=openai.api_key)
    embeddings = OpenAIEmbeddings(openai_api_key=openai.api_key, chunk_size=2048)


    generator = TestsetGenerator.from_langchain(
        generator_llm,
        generator_llm,
        embeddings
    )
    testset = generator.generate_with_langchain_docs(documents, test_size=5, distributions={simple: 0.5, reasoning: 0.25, multi_context: 0.25})
    df = testset.to_pandas()
    print(df)
    #先將地端的結果放進來
    # df = pd.read_excel('testset.xlsx', engine='openpyxl')
    # 假设df是你之前生成的testset的DataFrame
    questions = df["question"].tolist()
    contexts = df["contexts"].tolist()
    ground_truths = df["ground_truth"].tolist()
    print(questions)

if __name__ == "__main__":
    asyncio.run(main(), debug=True)