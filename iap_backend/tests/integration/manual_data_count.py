import numpy as np
import datetime
import pandas as pd
import sys, os
import datetime
import pickle

sys.path.extend(['.', '..'])
from HandleRequest.peface import iap_report
from HandleRequest.aeface import iap_ae_report_v1, iap_ae_report, iap_ae_report_0
from common.utils import list_all_files
from containers import Container
from configs.config import rag_start_time, rag_end_time
from collections import defaultdict
import tqdm

container = Container()
container.wire(modules=[__name__])

def check_source_db(start_date: datetime.date, end_date: datetime.date):
    iap_db = container.iap_db()
    table_manager = container.iap_table_manager_repository()

    # 查詢 REPORT_LIST 並篩選日期
    report_list = table_manager.query_table(table_name="REPORT_LIST")
    
    # 將 UPLOAD_DATE（字串）轉為 datetime 格式，假設格式為 '2025/05/01'
    report_list["UPLOAD_DATE_PARSED"] = pd.to_datetime(report_list["UPLOAD_DATE"], format="%Y/%m/%d", errors="coerce")

    report_list = report_list[
        (report_list["UPLOAD_DATE_PARSED"] >= pd.Timestamp(start_date)) &
        (report_list["UPLOAD_DATE_PARSED"] <= pd.Timestamp(end_date))
    ]

    issue_id = np.unique(report_list["ISSUE_ID"])
    print(f"issue_id:{len(issue_id)} 筆")

    # 查詢附件表格
    report_list_attachment = table_manager.query_table(table_name="REPORT_LIST_ATTACHMENT")
    report_list_attachment["UPLOAD_DATE_PARSED"] = pd.to_datetime(report_list_attachment["CREATE_TIME"], format="%Y-%m-%d %h:%M:%s", errors="coerce")

    report_list_attachment = report_list_attachment[
        (report_list_attachment["UPLOAD_DATE_PARSED"] >= pd.Timestamp(start_date)) &
        (report_list_attachment["UPLOAD_DATE_PARSED"] <= pd.Timestamp(end_date))
    ]

    # 篩選 field name 包含 pic / file 的資料
    att_pic_df = report_list_attachment[report_list_attachment["FIELD_NAME"].str.contains("pic", case=False, na=False)]
    att_file_df = report_list_attachment[report_list_attachment["FIELD_NAME"].str.contains("file", case=False, na=False)]

    print(f"附件檔案（file）：{len(att_file_df)} 筆")
    print(f"附件照片（pic）：{len(att_pic_df)} 筆")

def check_local_file(start_date: datetime.date, end_date: datetime.date):
    
    

    input_dir = "/app/rag_doc/iap"
    all_files = list_all_files(input_dir)

    pic_extensions = {'.jpg', '.jpeg', '.png', '.gif', '.bmp', '.tiff', '.webp'}

    pic_sizes = []
    file_sizes = []

    for path in all_files:
        if not os.path.isfile(path):
            continue

        # 從 path 中抓出日期（範例: /app/rag_doc/iap/2025/202504/20250402/...）
        try:
            parts = path.split("/")
            yyyymmdd_str = parts[6]  # parts[6] = '20250402'
            file_date = datetime.datetime.strptime(yyyymmdd_str, "%Y%m%d").date()
        except (IndexError, ValueError):
            continue  # 無法解析日期，跳過

        # 篩選日期區間
        if not (start_date <= file_date <= end_date):
            continue

        ext = os.path.splitext(path)[-1].lower()
        size = os.path.getsize(path)

        if ext in pic_extensions:
            pic_sizes.append(size)
        else:
            file_sizes.append(size)

    def calc_stats(sizes):
        if not sizes:
            return (0, 0, 0, 0)
        total = sum(sizes)
        count = len(sizes)
        avg = total / count
        max_size = max(sizes)
        return total, count, avg, max_size

    total_file, count_file, avg_file, max_file = calc_stats(file_sizes)
    total_pic, count_pic, avg_pic, max_pic = calc_stats(pic_sizes)

    print(f"📄 檔案：{count_file} 筆，總大小：{total_file/1_048_576:.2f} MB，平均：{avg_file/1_048_576:.2f} MB，最大：{max_file/1_048_576:.2f} MB")
    print(f"🖼️ 圖片：{count_pic} 筆，總大小：{total_pic/1_048_576:.2f} MB，平均：{avg_pic/1_048_576:.2f} MB，最大：{max_pic/1_048_576:.2f} MB")



def get_date_list(start_date:str, end_date:str):
    if end_date == "":
        end_date = datetime.datetime.now() # datetime.datetime(2024, 10, 2)
    else:
        end_date=end_date.split('/')
        end_date = datetime.datetime(int(end_date[0]), int(end_date[1]), int(end_date[2]))
    if start_date == "":
        start_date = datetime.datetime(2024, 10, 1)
    else:
        start_date=start_date.split('/')
        start_date = datetime.datetime(int(start_date[0]), int(start_date[1]), int(start_date[2]))
    pass
    date_list = []
    # 生成從開始日期到结束日期的所有日期
    current_date = start_date
    while current_date <= end_date:
        date_string = current_date.strftime("%Y/%m/%d")
        date_list.append(date_string)
        current_date += datetime.timedelta(days=1)  # 每次增加一天
    return date_list

if __name__ == "__main__":
    project_name = "AE"
    if 0:
        if project_name == "PE":
            input_dir = f"/app/rag_doc/iap"
        else:
            input_dir = f"/app/rag_doc/ae_sop"
        all_files = list_all_files(input_dir) # 取得所有檔案路徑
        relatedFile = [p for p in all_files if '.png' not in p.lower() and '.jpg' not in p.lower()]

        print(len(relatedFile))
        
    # 統計與計算數據量
    if 1:
        start_date="2026/01/01"
        end_date="2026/03/24"
        # 使用雙層字典：外層為日期/月份，內層為廠區
        daily_plant_counts = defaultdict(lambda: defaultdict(int))
        monthly_plant_counts = defaultdict(lambda: defaultdict(int))
        date_lst = get_date_list(start_date=start_date, end_date=end_date) # 取得日期列表
        pass
        progress_bar = tqdm.tqdm(total=len(date_lst), desc="計算數據量統計中...")
        for date in date_lst:
            if project_name == "AE":
                response = iap_ae_report_0(date)
            else:
                response = iap_report(date)
            data_lst = response['res']['data']
            data_count = len(data_lst)
            month = date[:7] 
            # 存取一下鍵值，確保即使當天沒有數據，日期跟月份仍會出現在表格的列中
            daily_plant_counts[date]
            monthly_plant_counts[month]
            
            # 遍歷每筆資料，針對對應的日期、月份與廠區進行 +1 計數
            for data in data_lst:
                plant = data['basic_information'].get('PLANT', 'Unknown')
                daily_plant_counts[date][plant] += 1
                monthly_plant_counts[month][plant] += 1
                
            progress_bar.update(1)
        progress_bar.close()
        
        # 將字典轉換為 DataFrame，將缺失的廠區補 0，並轉為整數
        df_daily = pd.DataFrame.from_dict(daily_plant_counts, orient='index').fillna(0).astype(int)
        df_daily.index.name = '日期'
        df_daily.reset_index(inplace=True)
        # 加上這行：確保日期由小到大排序
        df_daily.sort_values(by='日期', inplace=True, ignore_index=True)

        df_monthly = pd.DataFrame.from_dict(monthly_plant_counts, orient='index').fillna(0).astype(int)
        df_monthly.index.name = '月份'
        df_monthly.reset_index(inplace=True)
        # 加上這行：確保月份由小到大排序
        df_monthly.sort_values(by='月份', inplace=True, ignore_index=True)
        
        output_dir = "/app/tests/reports"
        os.makedirs(output_dir, exist_ok=True)
        df_daily.to_excel(f"{output_dir}/{project_name}_FACA_daily_{start_date.replace('/', '_')}_{end_date.replace('/', '_')}.xlsx")
        df_monthly.to_excel(f"{output_dir}/{project_name}_FACA_monthly_{start_date.replace('/', '_')}_{end_date.replace('/', '_')}.xlsx")
        
        print("\n【表格一：每日各廠區數據量變化】")
        print(df_daily.to_markdown(index=False))

        print("\n【表格二：每月各廠區數據量變化】")
        print(df_monthly.to_markdown(index=False))
        pass
        pass