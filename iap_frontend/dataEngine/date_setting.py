import pandas as pd
from datetime import datetime, timedelta

def set_date_variables(current_datetime):
    current_date_str = current_datetime.strftime("%Y-%m-%d")
    current_date = datetime.strptime(current_date_str, "%Y-%m-%d")

    today = current_date.strftime("%Y-%m-%d")
    yesterday = (current_date - timedelta(days=1)).strftime("%Y-%m-%d")
    current_month = current_date.strftime("%Y-%m")
    last_month_date = current_date.replace(day=1) - timedelta(days=1)
    last_month = last_month_date.strftime("%Y-%m")
    current_year = current_date.strftime("%Y")
    last_year_date = current_date.replace(year=current_date.year - 1)
    last_year = last_year_date.strftime("%Y")

    if current_datetime.weekday() == 6:
        this_week_start = current_date
        this_week_end = current_date + timedelta(days=6)
        last_week_start = current_date - timedelta(days=7)
        last_week_end = last_week_start + timedelta(days=6)
    else:
        this_week_start = current_date - timedelta(days=(current_datetime.weekday() + 1))
        this_week_end = this_week_start + timedelta(days=6)
        last_week_start = this_week_start - timedelta(days=7)
        last_week_end = last_week_start + timedelta(days=6)

    this_week_start_str = this_week_start.strftime("%Y-%m-%d")
    this_week_end_str = this_week_end.strftime("%Y-%m-%d")
    last_week_start_str = last_week_start.strftime("%Y-%m-%d")
    last_week_end_str = last_week_end.strftime("%Y-%m-%d")

    this_week_range = f"{this_week_start.strftime('%Y年%m月%d日')}至{this_week_end.strftime('%Y年%m月%d日')}"
    last_week_range = f"{last_week_start.strftime('%Y年%m月%d日')}至{last_week_end.strftime('%Y年%m月%d日')}"

    return today, yesterday, current_month, last_month, current_year, last_year, this_week_start_str, this_week_end_str, last_week_start_str, last_week_end_str, this_week_range, last_week_range

