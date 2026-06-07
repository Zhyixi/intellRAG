import os,sys
sys.path.extend(['.', '..'])
from pdf2image import convert_from_path
from PIL import Image
from PyPDF2 import PdfReader
from tqdm import tqdm
from io import BytesIO
import base64
import logging, re

def convert_pdf_to_images(pdf_file, dpi=600):
    # 使用 PyPDF2 獲取 PDF 總頁數
    reader = PdfReader(pdf_file)
    total_pages = len(reader.pages)
    progressbar = tqdm(desc="PDF2Image",total=total_pages)
    images = []
    # 逐頁處理
    for page_number in range(1, total_pages + 1):
        progressbar.update(1)
        page_image = convert_from_path(pdf_file, first_page=page_number, last_page=page_number, dpi=100)
        images.append(page_image[0])  # 轉換後返回的結果是 List，取第一個元素
    progressbar.close()
    return images



def build_pdf_image(file_path):
    if '.pdf' not in file_path:
        raise AssertionError(f"Only support pdf, your file:{file_path}")
    logging.info(f"transform to image...{file_path}")
    # 使用正則表達式去除最後一個"."及其後的部分
    path_without_extension = re.sub(r'\.[^\.]+$', '', file_path)
    output_folder = path_without_extension.replace("rag_doc/", "db/images/")
    os.makedirs(output_folder, exist_ok=True)
    images = convert_pdf_to_images(pdf_file=file_path)
    progress_bar = tqdm(desc="存截圖..",total=len(images))
    output_paths = []
    image_base64_list = []
    for i, image in enumerate(images):
        image_path = os.path.join(output_folder, f"{i+1}.jpg") # 影像儲存路徑
        if not os.path.exists(image_path):
            buf = BytesIO()
            image.save(buf, format='PNG')
            buf.seek(0)
            image_base64 = base64.b64encode(buf.getvalue()).decode()
            image_base64_list.append(image_base64)
            # 存檔
            image.save(image_path, "JPEG")
        output_paths.append(image_path)
        progress_bar.update(1)
    progress_bar.close()
    return output_paths