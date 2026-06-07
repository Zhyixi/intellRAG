import torch
from typing import List, Dict, Any
from pydantic import Field
from PIL import Image
from langchain_core.retrievers import BaseRetriever
from langchain_core.documents import Document
from colpali_engine.models import ColPali, ColPaliProcessor
# pip install colpali-engine langchain-core torch pillow
# ==========================================
# 1. 核心 Service：封裝 ColPali 的底層運算
# ==========================================
class ColPaliService:
    def __init__(self, model_name: str = "vidore/colpali-v1.2", device: str = "cuda"):
        self.device = device
        self.processor = ColPaliProcessor.from_pretrained(model_name)
        # 使用 bfloat16 節省 VRAM
        self.model = ColPali.from_pretrained(model_name, torch_dtype=torch.bfloat16).to(device).eval()

    def embed_images(self, images: List[Image.Image]) -> List[torch.Tensor]:
        """將 PDF 頁面影像轉換為多維度向量"""
        inputs = self.processor(images=images, return_tensors="pt").to(self.device)
        with torch.no_grad():
            embeddings = self.model(**inputs)
        return list(torch.unbind(embeddings.to("cpu")))

    def embed_query(self, query: str) -> torch.Tensor:
        """將使用者的查詢轉為多維度向量"""
        inputs = self.processor(text=query, return_tensors="pt").to(self.device)
        with torch.no_grad():
            embedding = self.model(**inputs)[0].to("cpu")
        return embedding

    def calculate_maxsim(self, query_emb: torch.Tensor, doc_embs: List[torch.Tensor]) -> List[float]:
        """計算 ColBERT 風格的 MaxSim 分數"""
        scores = []
        for doc_emb in doc_embs:
            # 矩陣相乘計算 token 間的相似度
            sim = torch.einsum("qd,vd->qv", query_emb, doc_emb)
            # 針對 Document tokens 取最大值，然後將 Query tokens 的分數加總
            score = sim.max(dim=1).values.sum().item()
            scores.append(score)
        return scores

# ==========================================
# 2. LangChain 元件：自訂 Retriever 並注入 Service
# ==========================================
class ColPaliRetriever(BaseRetriever):
    # 依賴注入的 Service
    service: ColPaliService = Field(description="Injected ColPali Service instance")
    
    # 記憶體內部的簡易儲存 (生產環境建議改為連接 Qdrant 或 Milvus)
    documents: List[Document] = Field(default_factory=list, description="Stored documents")
    doc_embeddings: List[torch.Tensor] = Field(default_factory=list, description="Stored multi-vectors")
    top_k: int = Field(default=3, description="Number of documents to return")

    def add_documents(self, images: List[Image.Image], metadata_list: List[Dict[str, Any]]):
        """處理圖片並建立索引"""
        embs = self.service.embed_images(images)
        self.doc_embeddings.extend(embs)
        
        for meta in metadata_list:
            # LangChain Document 必須有 page_content，此處可以留空或存放 OCR 備份，主要資訊存在 metadata
            self.documents.append(Document(page_content="", metadata=meta))

    def _get_relevant_documents(self, query: str, *, run_manager=None) -> List[Document]:
        """LangChain 檢索核心邏輯"""
        if not self.doc_embeddings:
            return []
        
        query_emb = self.service.embed_query(query)
        scores = self.service.calculate_maxsim(query_emb, self.doc_embeddings)
        
        # 將文件與分數綁定並排序
        scored_docs = sorted(zip(scores, self.documents), key=lambda x: x[0], reverse=True)
        
        # 回傳 Top K 的 Document (可視需求將分數也塞進 metadata 中)
        results = []
        for score, doc in scored_docs[:self.top_k]:
            doc.metadata["score"] = score
            results.append(doc)
            
        return results

# ==========================================
# 3. 系統組裝與調用 (Dependency Injection Structure)
# ==========================================
if __name__ == "__main__":
    # 1. 實例化 Service (若有使用 dependency-injector，此步驟在 Container 內定義)
    colpali_service = ColPaliService()

    # 2. 將 Service 注入到 Retriever
    retriever = ColPaliRetriever(service=colpali_service, top_k=2)
    # 3. 模擬載入 PDF 截圖並加入索引 (實戰中搭配 pdf2image 使用)
    real_image = Image.open('db/images/ae_sop/MR-JET用户手册(故障排除篇)/116.jpg').convert('RGB')
    retriever.add_documents([real_image], [{"source": "report.pdf", "page": 1}])
    # 4. LangChain 標準調用介面
    docs = retriever.invoke("伺服電機連接錯誤應該如何處理？")
    for doc in docs:
        print(f"匹配頁面: {doc.metadata['page']}, 分數: {doc.metadata['score']}")