# coding:utf-8

import sys
import logging
from logging.handlers import TimedRotatingFileHandler
import os, datetime
from common.utils import get_data_dir
from common.db import Database_Connection ,get_table_name

DATA_DIR = get_data_dir()
# SMT_CHECK_RUN_CUMULATIVE_PROCESS = get_table_name('SMT_CHECK_RUN_CUMULATIVE_PROCESS')

def init_logging(filename_prefix='?',site='', plant_code='',customer=''):
    site_log = 'log/common' if (site=='')|(plant_code=='')|(customer=='') else  f'log/{site}_{plant_code}_{customer}'
    LOG_PATH = os.path.join(f'{DATA_DIR}',f'{site_log}')
    if not os.path.exists(LOG_PATH):
        os.makedirs(LOG_PATH)

    root = logging.getLogger()
    root.handlers=[]
    level = logging.INFO
    filename = f'{LOG_PATH}/{filename_prefix}_{datetime.datetime.now().strftime("%Y-%m-%d")}.log'
    #logformat = '%(asctime)s %(levelname)s %(module)s.%(funcName)s Line:%(lineno)d %(message)s'
    logformat = '%(asctime)s %(levelname)s: %(message)s (%(filename)s:%(lineno)d)'
    timeformat = "%m-%d %H:%M:%S"
    logFormatter = logging.Formatter(logformat, timeformat)
    
    hdlr = TimedRotatingFileHandler(filename, "midnight", 1, 14)
    hdlr.setFormatter(logFormatter)
    root.addHandler(hdlr)
    root.setLevel(level)
    
    consoleHandler = logging.StreamHandler(sys.stdout)
    consoleHandler.setFormatter(CustomFormatter())
    consoleHandler.setLevel(logging.INFO)
    root.addHandler(consoleHandler)
    root.setLevel(level)


class CustomFormatter(logging.Formatter):

    grey = "\x1b[38;20m"
    yellow = "\x1b[33;20m"
    red = "\x1b[31;20m"
    bold_red = "\x1b[31;1m"
    reset = "\x1b[0m"
    format = "%(asctime)s %(levelname)s: %(message)s (%(filename)s:%(lineno)d)"

    FORMATS = {
        logging.DEBUG: grey + format + reset,
        logging.INFO: grey + format + reset,
        logging.WARNING: yellow + format + reset,
        logging.ERROR: red + format + reset,
        logging.CRITICAL: bold_red + format + reset
    }

    def format(self, record):
        log_fmt = self.FORMATS.get(record.levelno)
        formatter = logging.Formatter(log_fmt)
        return formatter.format(record)    
    


class CheckCumulativeProcess(object):
    
    _defaults = {
        'is_online' : False,
        'site': 'P3',
        'plant_code' : 'CN53',
        'customer' : 'A31',
        'process': 'test'
    }
    @classmethod
    def get_defaults(cls, n):
        if n in cls._defaults:
            return cls._defaults[n]
        else:
            return "Unrecognized attribute name '" + n + "'"

    def __init__(self, **kwargs):
        self.__dict__.update(self._defaults)
        self.__dict__.update(kwargs)
        self.db = Database_Connection(is_online=self.is_online)

    def check_cumulative_run(self):
        query = f"SELECT * FROM {SMT_CHECK_RUN_CUMULATIVE_PROCESS} WHERE PROCESS = '{self.process}' AND SITE = '{self.site}' \
                AND PLANT = '{self.plant_code}' AND CUSTOMER = '{self.customer}'"
        df = self.db.read_sql(query)
        date = df['SUCCESS_RUN_DATE'].values[0] if len(df) > 0 else ''
        today_has_run = datetime.date.today() == date

        need_run = True  if (len(df)==0)  | ((len(df)>0) & (today_has_run == False)) else False
        new_process = True if len(df)==0 else False

        return need_run, new_process

    def update_check_run_table(self):
        sql = f"UPDATE {SMT_CHECK_RUN_CUMULATIVE_PROCESS} SET SUCCESS_RUN_DATE = '{datetime.date.today()}' \
                WHERE PROCESS = '{self.process}' AND SITE = '{self.site}' AND PLANT = '{self.plant_code}' AND CUSTOMER = '{self.customer}'"
        self.db.execute_sql(sql)

    def insert_check_run_table(self):
        sql = f"""INSERT INTO `{SMT_CHECK_RUN_CUMULATIVE_PROCESS}` (PROCESS, SITE, PLANT, CUSTOMER, SUCCESS_RUN_DATE) \
                VALUES ("{self.process}", "{self.site}", "{self.plant_code}", "{self.customer}", "{datetime.date.today()}")"""
        self.db.execute_sql(sql)