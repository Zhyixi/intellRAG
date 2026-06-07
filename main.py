from fastapi import FastAPI, HTTPException, Depends, status
from pydantic import BaseModel
from typing import List, Optional
import uvicorn
from contextlib import asynccontextmanager

# --- Pydantic Models ---
class ItemBase(BaseModel):
    name: str
    description: Optional[str] = None
    price: float

class ItemCreate(ItemBase):
    pass

class Item(ItemBase):
    id: int
    
    class Config:
        orm_mode = True

# --- 模擬資料庫 ---
items_db = []
item_id_counter = 1

# --- FastAPI 應用程式生命週期 ---
@asynccontextmanager
async def lifespan(app: FastAPI):
    # 啟動時執行
    print("應用程式啟動...")
    yield
    # 關閉時執行
    print("應用程式關閉...")

# --- FastAPI 應用程式實例 ---
app = FastAPI(
    title="FastAPI Example",
    description="這是一個基本的 FastAPI 範例，展示了 CRUD 操作",
    version="1.0.0",
    lifespan=lifespan
)

# --- 路由和端點 ---

# GET: 根路徑
@app.get("/")
async def root():
    """根路徑返回歡迎訊息"""
    return {"message": "歡迎使用 FastAPI!"}

# GET: 獲取所有項目
@app.get("/items/", response_model=List[Item], tags=["items"])
async def read_items(skip: int = 0, limit: int = 10):
    """
    獲取所有項目
    - skip: 跳過前面幾個項目
    - limit: 返回最大項目數
    """
    return items_db[skip : skip + limit]

# GET: 獲取單個項目
@app.get("/items/{item_id}", response_model=Item, tags=["items"])
async def read_item(item_id: int):
    """根據ID獲取特定項目"""
    item = next((item for item in items_db if item["id"] == item_id), None)
    if item is None:
        raise HTTPException(status_code=404, detail="Item not found")
    return item

# POST: 創建新項目
@app.post("/items/", response_model=Item, status_code=status.HTTP_201_CREATED, tags=["items"])
async def create_item(item: ItemCreate):
    """創建新項目"""
    global item_id_counter
    new_item = item.dict()
    new_item["id"] = item_id_counter
    item_id_counter += 1
    items_db.append(new_item)
    return new_item

# PUT: 更新項目
@app.put("/items/{item_id}", response_model=Item, tags=["items"])
async def update_item(item_id: int, item: ItemCreate):
    """更新現有項目"""
    for existing_item in items_db:
        if existing_item["id"] == item_id:
            update_data = item.dict()
            existing_item.update(update_data)
            return existing_item
    raise HTTPException(status_code=404, detail="Item not found")

# DELETE: 刪除項目
@app.delete("/items/{item_id}", status_code=status.HTTP_204_NO_CONTENT, tags=["items"])
async def delete_item(item_id: int):
    """刪除特定項目"""
    for idx, item in enumerate(items_db):
        if item["id"] == item_id:
            items_db.pop(idx)
            return
    raise HTTPException(status_code=404, detail="Item not found")

# --- 中間件 ---
@app.middleware("http")
async def add_process_time_header(request, call_next):
    import time
    start_time = time.time()
    response = await call_next(request)
    process_time = time.time() - start_time
    response.headers["X-Process-Time"] = str(process_time)
    return response

# --- 啟動服務器 ---
if __name__ == "__main__":
    uvicorn.run(app, host="0.0.0.0", port=8000)