import os
import re
import json
import yaml
import httpx
import asyncio

from typing import Union
from pathlib import Path

from crawlers.utils.logger import logger
from crawlers.douyin.web.xbogus import XBogus as XB
from crawlers.utils.utils import (
    gen_random_str,
    get_timestamp,
    extract_valid_urls,
    split_filename,
)
from crawlers.utils.api_exceptions import (
    APIError,
    APIConnectionError,
    APIResponseError,
    APIUnauthorizedError,
    APINotFoundError,
)

# 配置文件路径
# Read the configuration file
path = os.path.abspath(os.path.dirname(__file__))

# 读取配置文件
with open(f"{path}/config.yaml", "r", encoding="utf-8") as f:
    config = yaml.safe_load(f)


class TokenManager:
    tiktok_manager = config.get("TokenManager").get("tiktok")
    token_conf = tiktok_manager.get("msToken", None)
    ttwid_conf = tiktok_manager.get("ttwid", None)
    odin_tt_conf = tiktok_manager.get("odin_tt", None)
    proxies_conf = tiktok_manager.get("proxies", None)
    proxies = {
        "http://": proxies_conf.get("http", None),
        "https://": proxies_conf.get("https", None),
    }

    @classmethod
    def gen_real_msToken(cls) -> str:
        """
        生成真实的msToken,当出现错误时返回虚假的值
        (Generate a real msToken and return a false value when an error occurs)
        """

        payload = json.dumps(
            {
                "magic": cls.token_conf["magic"],
                "version": cls.token_conf["version"],
                "dataType": cls.token_conf["dataType"],
                "strData": cls.token_conf["strData"],
                "tspFromClient": get_timestamp(),
            }
        )

        headers = {
            "User-Agent": cls.token_conf["User-Agent"],
            "Content-Type": "application/json",
        }

        transport = httpx.HTTPTransport(retries=5)
        with httpx.Client(transport=transport, proxies=cls.proxies) as client:
            try:
                response = client.post(
                    cls.token_conf["url"], headers=headers, content=payload
                )
                response.raise_for_status()

                msToken = str(httpx.Cookies(response.cookies).get("msToken"))

                return msToken

            # except httpx.RequestError as exc:
            #     # 捕获所有与 httpx 请求相关的异常情况 (Captures all httpx request-related exceptions)
            #     raise APIConnectionError("请求端点失败，请检查当前网络环境。 链接：{0}，代理：{1}，异常类名：{2}，异常详细信息：{3}"
            #                              .format(cls.token_conf["url"], cls.proxies, cls.__name__, exc)
            #                              )
            #
            # except httpx.HTTPStatusError as e:
            #     # 捕获 httpx 的状态代码错误 (captures specific status code errors from httpx)
            #     if response.status_code == 401:
            #         raise APIUnauthorizedError("参数验证失败，请更新 Douyin_TikTok_Download_API 配置文件中的 {0}，以匹配 {1} 新规则"
            #                                    .format("msToken", "tiktok")
            #                                    )
            #
            #     elif response.status_code == 404:
            #         raise APINotFoundError("{0} 无法找到API端点".format("msToken"))
            #     else:
            #         raise APIResponseError("链接：{0}，状态码 {1}：{2} ".format(
            #             e.response.url, e.response.status_code, e.response.text
            #         )
            #         )

            except Exception as e:
                # 返回虚假的msToken (Return a fake msToken)
                logger.error("生成TikTok msToken API错误：{0}".format(e))
                logger.info("当前网络无法正常访问TikTok服务器，已经使用虚假msToken以继续运行。")
                logger.info("并且TikTok相关API大概率无法正常使用，请在(/tiktok/web/config.yaml)中更新代理。")
                logger.info("如果你不需要使用TikTok相关API，请忽略此消息。")
                return cls.gen_false_msToken()

    @classmethod
    def gen_false_msToken(cls) -> str:
        """生成随机msToken (Generate random msToken)"""
        return gen_random_str(146) + "=="

    @classmethod
    def gen_ttwid(cls, cookie: str) -> str:
        """
        生成请求必带的ttwid (Generate the essential ttwid for requests)
        """
        transport = httpx.HTTPTransport(retries=5)
        with httpx.Client(transport=transport, proxies=cls.proxies) as client:
            try:
                response = client.post(
                    cls.ttwid_conf["url"],
                    content=cls.ttwid_conf["data"],
                    headers={
                        "Cookie": cookie,
                        "Content-Type": "text/plain",
                    },
                )
                response.raise_for_status()

                ttwid = httpx.Cookies(response.cookies).get("ttwid")

                if ttwid is None:
                    raise APIResponseError(
                        "ttwid: 检查没有通过, 请更新配置文件中的ttwid"
                    )

                return ttwid

            except httpx.RequestError as exc:
                # 捕获所有与 httpx 请求相关的异常情况 (Captures all httpx request-related exceptions)
                raise APIConnectionError("请求端点失败，请检查当前网络环境。 链接：{0}，代理：{1}，异常类名：{2}，异常详细信息：{3}"
                                         .format(cls.ttwid_conf["url"], cls.proxies, cls.__name__, exc)
                                         )

            except httpx.HTTPStatusError as e:
                # 捕获 httpx 的状态代码错误 (captures specific status code errors from httpx)
                if response.status_code == 401:
                    raise APIUnauthorizedError("参数验证失败，请更新 Douyin_TikTok_Download_API 配置文件中的 {0}，以匹配 {1} 新规则"
                                               .format("ttwid", "tiktok")
                                               )

                elif response.status_code == 404:
                    raise APINotFoundError("{0} 无法找到API端点".format("ttwid"))
                else:
                    raise APIResponseError("链接：{0}，状态码 {1}：{2} ".format(
                        e.response.url, e.response.status_code, e.response.text
                    )
                    )

    @classmethod
    def gen_odin_tt(cls):
        """
        生成请求必带的odin_tt (Generate the essential odin_tt for requests)
        """
        transport = httpx.HTTPTransport(retries=5)
        with httpx.Client(transport=transport, proxies=cls.proxies) as client:
            try:
                response = client.get(cls.odin_tt_conf["url"])
                response.raise_for_status()

                odin_tt = httpx.Cookies(response.cookies).get("odin_tt")

                if odin_tt is None:
                    raise APIResponseError("{0} 内容不符合要求".format("odin_tt"))

                return odin_tt

            except httpx.RequestError as exc:
                # 捕获所有与 httpx 请求相关的异常情况 (Captures all httpx request-related exceptions)
                raise APIConnectionError("请求端点失败，请检查当前网络环境。 链接：{0}，代理：{1}，异常类名：{2}，异常详细信息：{3}"
                                         .format(cls.odin_tt_conf["url"], cls.proxies, cls.__name__, exc)
                                         )

            except httpx.HTTPStatusError as e:
                # 捕获 httpx 的状态代码错误 (captures specific status code errors from httpx)
                if response.status_code == 401:
                    raise APIUnauthorizedError("参数验证失败，请更新 Douyin_TikTok_Download_API 配置文件中的 {0}，以匹配 {1} 新规则"
                                               .format("odin_tt", "tiktok")
                                               )

                elif response.status_code == 404:
                    raise APINotFoundError("{0} 无法找到API端点".format("odin_tt"))
                else:
                    raise APIResponseError("链接：{0}，状态码 {1}：{2} ".format(
                        e.response.url, e.response.status_code, e.response.text
                    )
                    )


class BogusManager:
    @classmethod
    def xb_str_2_endpoint(
            cls,
            user_agent: str,
            endpoint: str,
    ) -> str:
        try:
            final_endpoint = XB(user_agent).getXBogus(endpoint)
        except Exception as e:
            raise RuntimeError("生成X-Bogus失败: {0})".format(e))

        return final_endpoint[0]

    @classmethod
    def model_2_endpoint(
            cls,
            base_endpoint: str,
            params: dict,
            user_agent: str,
    ) -> str:
        # 检查params是否是一个字典 (Check if params is a dict)
        if not isinstance(params, dict):
            raise TypeError("参数必须是字典类型")

        param_str = "&".join([f"{k}={v}" for k, v in params.items()])

        try:
            xb_value = XB(user_agent).getXBogus(param_str)
        except Exception as e:
            raise RuntimeError("生成X-Bogus失败: {0})".format(e))

        # 检查base_endpoint是否已有查询参数 (Check if base_endpoint already has query parameters)
        separator = "&" if "?" in base_endpoint else "?"

        final_endpoint = f"{base_endpoint}{separator}{param_str}&X-Bogus={xb_value[1]}"

        return final_endpoint


class SecUserIdFetcher:
    # 预编译正则表达式
    _TIKTOK_SECUID_PARREN = re.compile(
        r"<script id=\"__UNIVERSAL_DATA_FOR_REHYDRATION__\" type=\"application/json\">(.*?)</script>"
    )
    _TIKTOK_UNIQUEID_PARREN = re.compile(r"/@([^/?]*)")
    _TIKTOK_NOTFOUND_PARREN = re.compile(r"notfound")

    @classmethod
    async def get_secuid(cls, url: str) -> str:
        """
        获取TikTok用户sec_uid
        Args:
            url: 用户主页链接
        Return:
            sec_uid: 用户唯一标识
        """

        # 进行参数检查
        if not isinstance(url, str):
            raise TypeError("输入参数必须是字符串")

        # 提取有效URL
        url = extract_valid_urls(url)

        if url is None:
            raise (
                APINotFoundError("输入的URL不合法。类名：{0}".format(cls.__name__))
            )

        transport = httpx.AsyncHTTPTransport(retries=5)
        async with httpx.AsyncClient(
                transport=transport, proxies=TokenManager.proxies, timeout=10
        ) as client:
            try:
                response = await client.get(url, follow_redirects=True)
                # 444一般为Nginx拦截，不返回状态 (444 is generally intercepted by Nginx and does not return status)
                if response.status_code in {200, 444}:
                    if cls._TIKTOK_NOTFOUND_PARREN.search(str(response.url)):
                        raise APINotFoundError("页面不可用，可能是由于区域限制（代理）造成的。类名: {0}"
                                               .format(cls.__name__)
                                               )

                    match = cls._TIKTOK_SECUID_PARREN.search(str(response.text))
                    if not match:
                        raise APIResponseError("未在响应中找到 {0}，检查链接是否为用户主页。类名: {1}"
                                               .format("sec_uid", cls.__name__)
                                               )

                    # 提取SIGI_STATE对象中的sec_uid
                    data = json.loads(match.group(1))
                    default_scope = data.get("__DEFAULT_SCOPE__", {})
                    user_detail = default_scope.get("webapp.user-detail", {})
                    user_info = user_detail.get("userInfo", {}).get("user", {})
                    sec_uid = user_info.get("secUid")

                    if sec_uid is None:
                        raise RuntimeError(
                            "获取 {0} 失败，{1}".format(sec_uid, user_info)
                        )

                    return sec_uid
                else:
                    raise ConnectionError("接口状态码异常, 请检查重试")

            except httpx.RequestError as exc:
                # 捕获所有与 httpx 请求相关的异常情况 (Captures all httpx request-related exceptions)
                raise APIConnectionError("请求端点失败，请检查当前网络环境。 链接：{0}，代理：{1}，异常类名：{2}，异常详细信息：{3}"
                                         .format(url, TokenManager.proxies, cls.__name__, exc)
                                         )

    @classmethod
    async def get_all_secuid(cls, urls: list) -> list:
        """
        获取列表secuid列表 (Get list sec_user_id list)

        Args:
            urls: list: 用户url列表 (User url list)

        Return:
            secuids: list: 用户secuid列表 (User secuid list)
        """

        if not isinstance(urls, list):
            raise TypeError("参数必须是列表类型")

        # 提取有效URL
        urls = extract_valid_urls(urls)

        if urls == []:
            raise (
                APINotFoundError(
                    "输入的URL List不合法。类名：{0}".format(cls.__name__)
                )
            )

        secuids = [cls.get_secuid(url) for url in urls]
        return await asyncio.gather(*secuids)

    @classmethod
    async def get_uniqueid(cls, url: str) -> str:
        """
        获取TikTok用户unique_id
        Args:
            url: 用户主页链接
        Return:
            unique_id: 用户唯一id
        """

        # 进行参数检查
        if not isinstance(url, str):
            raise TypeError("输入参数必须是字符串")

        # 提取有效URL
        url = extract_valid_urls(url)

        if url is None:
            raise (
                APINotFoundError("输入的URL不合法。类名：{0}".format(cls.__name__))
            )

        transport = httpx.AsyncHTTPTransport(retries=5)
        async with httpx.AsyncClient(
                transport=transport, proxies=TokenManager.proxies, timeout=10
        ) as client:
            try:
                response = await client.get(url, follow_redirects=True)

                if response.status_code in {200, 444}:
                    if cls._TIKTOK_NOTFOUND_PARREN.search(str(response.url)):
                        raise APINotFoundError("页面不可用，可能是由于区域限制（代理）造成的。类名: {0}"
                                               .format(cls.__name__)
                                               )

                    match = cls._TIKTOK_UNIQUEID_PARREN.search(str(response.url))
                    if not match:
                        raise APIResponseError(
                            "未在响应中找到 {0}".format("unique_id")
                        )

                    unique_id = match.group(1)

                    if unique_id is None:
                        raise RuntimeError(
                            "获取 {0} 失败，{1}".format("unique_id", response.url)
                        )

                    return unique_id
                else:
                    raise ConnectionError(
                        "接口状态码异常 {0}, 请检查重试".format(response.status_code)
                    )

            except httpx.RequestError:
                raise APIConnectionError("连接端点失败，检查网络环境或代理：{0} 代理：{1} 类名：{2}"
                                         .format(url, TokenManager.proxies, cls.__name__),
                                         )

    @classmethod
    async def get_all_uniqueid(cls, urls: list) -> list:
        """
        获取列表unique_id列表 (Get list sec_user_id list)

        Args:
            urls: list: 用户url列表 (User url list)

        Return:
            unique_ids: list: 用户unique_id列表 (User unique_id list)
        """

        if not isinstance(urls, list):
            raise TypeError("参数必须是列表类型")

        # 提取有效URL
        urls = extract_valid_urls(urls)

        if urls == []:
            raise (
                APINotFoundError(
                    "输入的URL List不合法。类名：{0}".format(cls.__name__)
                )
            )

        unique_ids = [cls.get_uniqueid(url) for url in urls]
        return await asyncio.gather(*unique_ids)


class AwemeIdFetcher:
    # https://www.tiktok.com/@scarlettjonesuk/video/7255716763118226715
    # https://www.tiktok.com/@scarlettjonesuk/video/7255716763118226715?is_from_webapp=1&sender_device=pc&web_id=7306060721837852167
    # https://www.tiktok.com/@zoyapea5/photo/7370061866879454469

    # 预编译正则表达式
    _TIKTOK_AWEMEID_PATTERN = re.compile(r"video/(\d+)")
    _TIKTOK_PHOTOID_PATTERN = re.compile(r"photo/(\d+)")
    _TIKTOK_NOTFOUND_PATTERN = re.compile(r"notfound")

    @classmethod
    async def get_aweme_id(cls, url: str) -> str:
        """
        获取TikTok作品aweme_id或photo_id
        Args:
            url: 作品链接
        Return:
            aweme_id: 作品唯一标识
        """

        # 进行参数检查
        if not isinstance(url, str):
            raise TypeError("输入参数必须是字符串")

        # 提取有效URL
        url = extract_valid_urls(url)

        if url is None:
            raise APINotFoundError("输入的URL不合法。类名：{0}".format(cls.__name__))

        # 处理不是短连接的情况
        if "tiktok" and "@" in url:
            print(f"输入的URL无需重定向: {url}")
            video_match = cls._TIKTOK_AWEMEID_PATTERN.search(url)
            photo_match = cls._TIKTOK_PHOTOID_PATTERN.search(url)

            if not video_match and not photo_match:
                raise APIResponseError("未在响应中找到 aweme_id 或 photo_id")

            aweme_id = video_match.group(1) if video_match else photo_match.group(1)

            if aweme_id is None:
                raise RuntimeError("获取 aweme_id 或 photo_id 失败，{0}".format(url))

            return aweme_id

        # 处理短连接的情况，根据重定向后的链接获取aweme_id
        print(f"输入的URL需要重定向: {url}")
        transport = httpx.AsyncHTTPTransport(retries=10)
        async with httpx.AsyncClient(
                transport=transport, proxies=TokenManager.proxies, timeout=10
        ) as client:
            try:
                response = await client.get(url, follow_redirects=True)

                if response.status_code in {200, 444}:
                    if cls._TIKTOK_NOTFOUND_PATTERN.search(str(response.url)):
                        raise APINotFoundError("页面不可用，可能是由于区域限制（代理）造成的。类名: {0}"
                                               .format(cls.__name__)
                                               )

                    video_match = cls._TIKTOK_AWEMEID_PATTERN.search(str(response.url))
                    photo_match = cls._TIKTOK_PHOTOID_PATTERN.search(str(response.url))

                    if not video_match and not photo_match:
                        raise APIResponseError("未在响应中找到 aweme_id 或 photo_id")

                    aweme_id = video_match.group(1) if video_match else photo_match.group(1)

                    if aweme_id is None:
                        raise RuntimeError("获取 aweme_id 或 photo_id 失败，{0}".format(response.url))

                    return aweme_id
                else:
                    raise ConnectionError("接口状态码异常 {0}，请检查重试".format(response.status_code))

            except httpx.RequestError as exc:
                # 捕获所有与 httpx 请求相关的异常情况
                raise APIConnectionError("请求端点失败，请检查当前网络环境。 链接：{0}，代理：{1}，异常类名：{2}，异常详细信息：{3}"
                                         .format(url, TokenManager.proxies, cls.__name__, exc)
                                         )

    @classmethod
    async def get_all_aweme_id(cls, urls: list) -> list:
        """
        获取视频aweme_id,传入列表url都可以解析出aweme_id (Get video aweme_id, pass in the list url can parse out aweme_id)

        Args:
            urls: list: 列表url (list url)

        Return:
            aweme_ids: list: 视频的唯一标识，返回列表 (The unique identifier of the video, return list)
        """

        if not isinstance(urls, list):
            raise TypeError("参数必须是列表类型")

        # 提取有效URL
        urls = extract_valid_urls(urls)

        if urls == []:
            raise (
                APINotFoundError(
                    "输入的URL List不合法。类名：{0}".format(cls.__name__)
                )
            )

        aweme_ids = [cls.get_aweme_id(url) for url in urls]
        return await asyncio.gather(*aweme_ids)


def format_file_name(
        naming_template: str,
        aweme_data: dict = {},
        custom_fields: dict = {},
) -> str:
    """
    根据配置文件的全局格式化文件名
    (Format file name according to the global conf file)

    Args:
        aweme_data (dict): 抖音数据的字典 (dict of douyin data)
        naming_template (str): 文件的命名模板, 如 "{create}_{desc}" (Naming template for files, such as "{create}_{desc}")
        custom_fields (dict): 用户自定义字段, 用于替代默认的字段值 (Custom fields for replacing default field values)

    Note:
        windows 文件名长度限制为 255 个字符, 开启了长文件名支持后为 32,767 个字符
        (Windows file name length limit is 255 characters, 32,767 characters after long file name support is enabled)
        Unix 文件名长度限制为 255 个字符
        (Unix file name length limit is 255 characters)
        取去除后的50个字符, 加上后缀, 一般不会超过255个字符
        (Take the removed 50 characters, add the suffix, and generally not exceed 255 characters)
        详细信息请参考: https://en.wikipedia.org/wiki/Filename#Length
        (For more information, please refer to: https://en.wikipedia.org/wiki/Filename#Length)

    Returns:
        str: 格式化的文件名 (Formatted file name)
    """

    # 为不同系统设置不同的文件名长度限制
    os_limit = {
        "win32": 200,
        "cygwin": 60,
        "darwin": 60,
        "linux": 60,
    }

    fields = {
        "create": aweme_data.get("createTime", ""),  # 长度固定19
        "nickname": aweme_data.get("nickname", ""),  # 最长30
        "aweme_id": aweme_data.get("aweme_id", ""),  # 长度固定19
        "desc": split_filename(aweme_data.get("desc", ""), os_limit),
        "uid": aweme_data.get("uid", ""),  # 固定11
    }

    if custom_fields:
        # 更新自定义字段
        fields.update(custom_fields)

    try:
        return naming_template.format(**fields)
    except KeyError as e:
        raise KeyError("文件名模板字段 {0} 不存在，请检查".format(e))


def create_user_folder(kwargs: dict, nickname: Union[str, int]) -> Path:
    """
    根据提供的配置文件和昵称，创建对应的保存目录。
    (Create the corresponding save directory according to the provided conf file and nickname.)

    Args:
        kwargs (dict): 配置文件，字典格式。(Conf file, dict format)
        nickname (Union[str, int]): 用户的昵称，允许字符串或整数。  (User nickname, allow strings or integers)

    Note:
        如果未在配置文件中指定路径，则默认为 "Download"。
        (If the path is not specified in the conf file, it defaults to "Download".)
        仅支持相对路径。
        (Only relative paths are supported.)

    Raises:
        TypeError: 如果 kwargs 不是字典格式，将引发 TypeError。
        (If kwargs is not in dict format, TypeError will be raised.)
    """

    # 确定函数参数是否正确
    if not isinstance(kwargs, dict):
        raise TypeError("kwargs 参数必须是字典")

    # 创建基础路径
    base_path = Path(kwargs.get("path", "Download"))

    # 添加下载模式和用户名
    user_path = (
            base_path / "tiktok" / kwargs.get("mode", "PLEASE_SETUP_MODE") / str(nickname)
    )

    # 获取绝对路径并确保它存在
    resolve_user_path = user_path.resolve()

    # 创建目录
    resolve_user_path.mkdir(parents=True, exist_ok=True)

    return resolve_user_path


def rename_user_folder(old_path: Path, new_nickname: str) -> Path:
    """
    重命名用户目录 (Rename User Folder).

    Args:
        old_path (Path): 旧的用户目录路径 (Path of the old user folder)
        new_nickname (str): 新的用户昵称 (New user nickname)

    Returns:
        Path: 重命名后的用户目录路径 (Path of the renamed user folder)
    """
    # 获取目标目录的父目录 (Get the parent directory of the target folder)
    parent_directory = old_path.parent

    # 构建新目录路径 (Construct the new directory path)
    new_path = old_path.rename(parent_directory / new_nickname).resolve()

    return new_path


def create_or_rename_user_folder(
        kwargs: dict, local_user_data: dict, current_nickname: str
) -> Path:
    """
    创建或重命名用户目录 (Create or rename user directory)

    Args:
        kwargs (dict): 配置参数 (Conf parameters)
        local_user_data (dict): 本地用户数据 (Local user data)
        current_nickname (str): 当前用户昵称 (Current user nickname)

    Returns:
        user_path (Path): 用户目录路径 (User directory path)
    """
    user_path = create_user_folder(kwargs, current_nickname)

    if not local_user_data:
        return user_path

    if local_user_data.get("nickname") != current_nickname:
        # 昵称不一致，触发目录更新操作
        user_path = rename_user_folder(user_path, current_nickname)

    return user_path
