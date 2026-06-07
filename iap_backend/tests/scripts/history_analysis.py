import os, sys
sys.path.extend(['.', '..'])
import datetime
import pandas as pd
import numpy as np
from services.services import MongoService
from containers import Container

def analyze_user_frequency(df, time_col='timestamp', window='20s'):
    # 1. 確保欄位為 datetime 格式，依時間排序
    df_calc = df.copy()
    df_calc[time_col] = pd.to_datetime(df_calc[time_col])
    df_calc = df_calc.sort_values(by=time_col).reset_index(drop=True)
    
    # 2. 計算 20 秒內的累積人數 (包含自己)
    temp_df = pd.DataFrame({'user_count': 1}, index=df_calc[time_col])
    rolling_counts = temp_df.rolling(window).sum()
    df_calc['concurrent_users'] = rolling_counts['user_count'].values
    
    # 3. 過濾人數大於 1 的資料
    filtered_df = df_calc[df_calc['concurrent_users'] > 1].copy()
    filtered_df['concurrent_users'] = filtered_df['concurrent_users'].astype(int)
    
    # 4. 針對不同人數進行整體分組統計
    summary = filtered_df.groupby('concurrent_users').agg(
        occurrences=(time_col, 'count'),
        first_seen=(time_col, 'min'),
        last_seen=(time_col, 'max')
    ).reset_index()
    
    summary = summary.rename(columns={
        'concurrent_users': '指定秒數內人數',
        'occurrences': '發生總次數',
        'first_seen': '首次發生時間',
        'last_seen': '最後發生時間'
    })
    
    return summary, filtered_df

def test_get_history_session_id(mongo_service, collection_name="iap_ae_Chat_History"):
    df = mongo_service.find_df(query={}, collection_name=collection_name)
    
    df['Role'] = df['Role'].astype(str)
    
    # 排除特定帳號
    exclude_roles = ["10038437", "10037016", "20675832", "10028005", '20880663']
    df = df[~df["Role"].isin(exclude_roles)]
    df['timestamp'] = pd.to_datetime(df['timestamp'])
    
    # 1. 建立時間與廠區維度欄位
    df['date'] = df['timestamp'].dt.date
    df['month'] = df['timestamp'].dt.strftime('%Y-%m')
    df['Site_Code'] = df['Role'].str[:3]
    
    # ---------------------------------------------------------
    # [併發人數分析 (指定秒數內 > 1人)]
    # ---------------------------------------------------------
    overall_concurrent_summary, concurrent_detail_df = analyze_user_frequency(df, time_col='timestamp', window='20s')
    
    # 將欄位名稱加上 "人" 方便閱讀 (例如 2 -> "2人")
    concurrent_detail_df['concurrent_users_label'] = concurrent_detail_df['concurrent_users'].astype(str) + "人"
    
    # 每日指定秒數內併發次數矩陣 (列: 日期, 欄: 人數)
    daily_concurrent_pivot = pd.pivot_table(
        concurrent_detail_df, index='date', columns='concurrent_users_label', aggfunc='size', fill_value=0
    )
    
    # 每月指定秒數內併發次數矩陣 (列: 月份, 欄: 人數)
    monthly_concurrent_pivot = pd.pivot_table(
        concurrent_detail_df, index='month', columns='concurrent_users_label', aggfunc='size', fill_value=0
    )

    # ---------------------------------------------------------
    # [個人與廠區維度分析]
    # ---------------------------------------------------------
    daily_person_pivot = pd.pivot_table(df, index='Role', columns='date', aggfunc='size', fill_value=0)
    monthly_person_pivot = pd.pivot_table(df, index='Role', columns='month', aggfunc='size', fill_value=0)
    
    daily_site_pivot = pd.pivot_table(df, index='Site_Code', columns='date', aggfunc='size', fill_value=0)
    monthly_site_pivot = pd.pivot_table(df, index='Site_Code', columns='month', aggfunc='size', fill_value=0)

    # ---------------------------------------------------------
    # [整體系統維度分析]
    # ---------------------------------------------------------
    daily_total_usage = df.groupby('date').size().reset_index(name='total_daily_usage')
    monthly_total_usage = df.groupby('month').size().reset_index(name='total_monthly_usage')

    # ---------------------------------------------------------
    # [匯出報表] 統整為單一 Excel 檔案，多個 Sheet
    # ---------------------------------------------------------
    output_dir = "/app/tests/reports"
    os.makedirs(output_dir, exist_ok=True)
    output_filename = f"{output_dir}/{collection_name}_Usage_Report.xlsx"
    # 若要在本地執行測試，可拿掉路徑改為： output_filename = f"{collection_name}_Usage_Report.xlsx"
    
    with pd.ExcelWriter(output_filename) as writer:
        # 新增的併發人數統計
        overall_concurrent_summary.to_excel(writer, sheet_name='整體併發總計', index=False)
        daily_concurrent_pivot.to_excel(writer, sheet_name='每日併發人數統計')
        monthly_concurrent_pivot.to_excel(writer, sheet_name='每月併發人數統計')
        
        # 原有的統計
        monthly_person_pivot.to_excel(writer, sheet_name='個人每月使用量')
        daily_person_pivot.to_excel(writer, sheet_name='個人每日使用量')
        monthly_site_pivot.to_excel(writer, sheet_name='廠區每月使用量')
        daily_site_pivot.to_excel(writer, sheet_name='廠區每日使用量')
        monthly_total_usage.to_excel(writer, sheet_name='整體每月趨勢', index=False)
        daily_total_usage.to_excel(writer, sheet_name='整體每日趨勢', index=False)
    
    print(f"\n報表已成功匯出至：{output_filename}")

if __name__ == '__main__':
    container = Container()
    test_get_history_session_id(container.mongo_service())