import datetime
from datetime import timedelta
def set_date_variables(current_datetime):
    # 确保 current_datetime 是 datetime 对象
    if not isinstance(current_datetime, datetime.datetime):
        raise TypeError("current_datetime 必须是 datetime 对象")
    
    # 获取当前日期
    current_date = current_datetime.date()
    
    # 设置今天的时间范围（从今天早上 8 点到当前时刻）
    today_start = datetime.datetime.combine(current_date, datetime.datetime.min.time()) + timedelta(hours=8)
    today_end = current_datetime
    
    # 设置昨天的时间范围（从昨天早上 8 点到今天早上 8 点）
    yesterday_start = datetime.datetime.combine(current_date - timedelta(days=1), datetime.datetime.min.time()) + timedelta(hours=8)
    yesterday_end = datetime.datetime.combine(current_date, datetime.datetime.min.time()) + timedelta(hours=8) - timedelta(seconds=1)
    
    # 設置這個月的範圍：從本月 1 日早上 8 點到下個月 1 日早上 7:59
    current_month_start = datetime.datetime.combine(current_date.replace(day=1), datetime.datetime.min.time()) + timedelta(hours=8)
    next_month_start = (current_date.replace(day=1) + timedelta(days=32)).replace(day=1)
    current_month_end = datetime.datetime.combine(next_month_start, datetime.datetime.min.time()) + timedelta(hours=7, minutes=59, seconds=59)
    
    # 設置上個月的範圍：從上個月 1 日早上 8 點到這個月 1 日早上 7:59
    last_month_date = (current_date.replace(day=1) - timedelta(days=1))
    last_month_start = datetime.datetime.combine(last_month_date.replace(day=1), datetime.datetime.min.time()) + timedelta(hours=8)
    last_month_end = current_month_start - timedelta(seconds=1)
    
    # 設置今年的範圍：從 1 月 1 日早上 8 點到明年 1 月 1 日早上 7:59
    current_year_start = datetime.datetime.combine(current_date.replace(month=1, day=1), datetime.datetime.min.time()) + timedelta(hours=8)
    next_year_start = current_date.replace(year=current_date.year + 1, month=1, day=1)
    current_year_end = datetime.datetime.combine(next_year_start, datetime.datetime.min.time()) + timedelta(hours=7, minutes=59, seconds=59)
    
    # 設置去年的範圍：從去年 1 月 1 日早上 8 點到今年 1 月 1 日早上 7:59
    last_year_date = current_date.replace(year=current_date.year - 1)
    last_year_start = datetime.datetime.combine(last_year_date.replace(month=1, day=1), datetime.datetime.min.time()) + timedelta(hours=8)
    last_year_end = current_year_start - timedelta(seconds=1)
    
    # 设置本周的时间范围（周一早上 8 点到本周末的 23:59:59）
    weekday = current_datetime.weekday()
    if weekday == 6:
        this_week_start = datetime.datetime.combine(current_date, datetime.datetime.min.time()) + timedelta(hours=8)
    else:
        this_week_start = datetime.datetime.combine(current_date - timedelta(days=weekday), datetime.datetime.min.time()) + timedelta(hours=8)
    
    this_week_end = this_week_start + datetime.timedelta(days=6, hours=23, minutes=59, seconds=59)
    
    # 设置上周的时间范围
    last_week_start = this_week_start - timedelta(days=7)
    last_week_end = last_week_start + timedelta(days=6, hours=23, minutes=59, seconds=59)
    
    this_week_start_str = this_week_start.strftime("%Y-%m-%d %H:%M:%S")
    this_week_end_str = this_week_end.strftime("%Y-%m-%d %H:%M:%S")
    last_week_start_str = last_week_start.strftime("%Y-%m-%d %H:%M:%S")
    last_week_end_str = last_week_end.strftime("%Y-%m-%d %H:%M:%S")
    
    return today_start,today_end, yesterday_start,yesterday_end, current_month_start,current_month_end, last_month_start, last_month_end , current_year_start, current_year_end , last_year_start, last_year_end , this_week_start_str, this_week_end_str, last_week_start_str, last_week_end_str

def preprocess_df_instr(df_instr:str):
    df_instr = df_instr.replace("```", "").replace("'''", "").replace("python", "").strip()
    df_instr = df_instr.replace("\n", "")
    df_instr=df_instr.lstrip('\`').rstrip('\`')
    df_instr = df_instr.replace("pydf", "")
    return df_instr