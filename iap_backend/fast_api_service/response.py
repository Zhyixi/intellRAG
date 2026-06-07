# coding:utf-8
import json

def initResponse():
    responseData = {}
    responseData['responseCode'] = 200
    responseData['responseMessage'] = 'OK'
    return responseData


def parseResponseJson(result):
    responseData= initResponse()
    for result in result.items():
        responseData[result[0]] = result[1]
    return responseData


def setErrorResponse(errorCode, errorMessage):
    """組裝錯誤 JSON；對外 API 請搭配 fast_api_service.api_errors 避免寫入 traceback。"""
    responseData = {}
    responseData['responseCode'] = errorCode
    responseData['responseMessage'] = errorMessage
    return responseData

