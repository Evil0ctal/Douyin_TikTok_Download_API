import re

from pywebio.output import get_scope, clear
from pywebio.session import info as session_info


class ViewsUtils:

    # 自动检测语言返回翻译/Auto detect language to return translation
    @staticmethod
    def t(zh: str, en: str) -> str:
        return zh if 'zh' in session_info.user_language else en

    # 清除前一个scope/Clear the previous scope
    @staticmethod
    def clear_previous_scope():
        _scope = get_scope(-1)
        clear(_scope)

    # 解析抖音分享口令中的链接并返回列表/Parse the link in the Douyin share command and return a list
    @staticmethod
    def find_url(string: str) -> list:
        url = re.findall('http[s]?://(?:[a-zA-Z]|[0-9]|[$-_@.&+]|[!*\(\),]|(?:%[0-9a-fA-F][0-9a-fA-F]))+', string)
        return url
