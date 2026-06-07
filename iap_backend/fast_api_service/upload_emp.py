
import os, sys
sys.path.extend(['.', '..'])
import pandas as pd
from dependency_injector.wiring import inject, Provide
from containers import Container
from repositories.repositories import TableManagerRepository

@inject
def update(table_name = "FACA_Employees", emp_file="/app/FACA_Employee.csv",
           table_manager: TableManagerRepository = Provide[Container.table_manager_repository]):
    df = pd.read_csv(emp_file)
    df = df.astype(str)
#     flag = Drop_table(table_name=table_name)
    flag = table_manager.insert_dataframe(df=df,table_name=table_name)
    df1 = table_manager.query_table(table_name=table_name)
    print(df1)
    pass