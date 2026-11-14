from httpx import AsyncClient, Response, TimeoutException, HTTPStatusError
from json import JSONDecodeError
from typing import Dict

from app.core.constants import HTTPX_UA


class HttpxOper:
    def __init__(self,httpx_client: AsyncClient):
        self._httpx_client = httpx_client

    async def _request(self,method:str,url:str,**kwargs)->Response:
        """
        发送请求通用方法
        :param method: 请求方法，get/post/put/delete/patch
        :param url: 请求url
        :param kwargs: headers/params/data/json/cookies
        :return:
        """
        request_func = getattr(self._httpx_client, method.lower())

        # 设置默认值
        headers = kwargs.setdefault("headers", {})
        kwargs.setdefault("timeout", 10)
        headers.setdefault("user-agent", HTTPX_UA)

        # 发送请求
        try:
            response:Response = await request_func(url=url,**kwargs)
            response.raise_for_status()
        except TimeoutException as e:
            raise TimeoutException(f"请求超时，url={url}") from e
        except HTTPStatusError as e:
            code = e.response.status_code
            raise HTTPStatusError(f"请求失败, url={url}，返回code: {code}",
                                  request=e.request,response=e.response) from e
        except Exception as e:
            raise Exception(f"请求失败, url={url}") from e

        return response

    async def get_json(self,url,**kwargs)->Dict:
        """
        发送get请求，结果json格式化
        """
        try:
            resp = await self._request("get",url,**kwargs)
            return resp.json()
        except JSONDecodeError as e:
            raise JSONDecodeError(f"JSON 解析失败, url={url}", e.doc, e.pos) from e

    async def post_json(self,url,**kwargs)->Dict:
        """
        发送post请求，结果json格式化
        """
        try:
            resp = await self._request("post", url, **kwargs)
            return resp.json()
        except JSONDecodeError as e:
            raise JSONDecodeError(f"JSON 解析失败, url={url}", e.doc, e.pos) from e

    async def get(self,url,**kwargs)->Response:
        return await self._request("get",url,**kwargs)
