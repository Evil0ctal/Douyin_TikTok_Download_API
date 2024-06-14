"""
Original Author:
This file is from https://github.com/JoeanAmier/TikTokDownloader
And is licensed under the GNU General Public License v3.0
If you use this code, please keep this license and the original author information.

Modified by:
And this file is now a part of the https://github.com/Evil0ctal/Douyin_TikTok_Download_API open-source project.
This project is licensed under the Apache License 2.0, and the original author information is kept.

Purpose:
This file is used to generate the `a_bogus` parameter for the Douyin Web API.

Changes Made:
1. Changed the ua_code to compatible with the current config file User-Agent string in https://github.com/Evil0ctal/Douyin_TikTok_Download_API/blob/main/crawlers/douyin/web/config.yaml
"""

from random import randint
from random import random
from re import compile
from time import time
from urllib.parse import urlencode, quote


class ABogus:
    __filter = compile(r'%([0-9A-F]{2})')
    __arguments = [0, 1, 14]
    __end_string = "cus"
    __version = [1, 0, 1, 5]
    __env = [
        49,
        53,
        51,
        54,
        124,
        55,
        52,
        50,
        124,
        49,
        53,
        51,
        54,
        124,
        56,
        54,
        52,
        124,
        48,
        124,
        48,
        124,
        48,
        124,
        48,
        124,
        49,
        53,
        51,
        54,
        124,
        56,
        54,
        52,
        124,
        49,
        53,
        51,
        54,
        124,
        56,
        54,
        52,
        124,
        49,
        53,
        51,
        54,
        124,
        55,
        52,
        50,
        124,
        50,
        52,
        124,
        50,
        52,
        124,
        87,
        105,
        110,
        51,
        50]
    __reg = [
        1937774191,
        1226093241,
        388252375,
        3666478592,
        2842636476,
        372324522,
        3817729613,
        2969243214,
    ]
    __str = {
        "s0": "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789+/=",
        "s1": "Dkdpgh4ZKsQB80/Mfvw36XI1R25+WUAlEi7NLboqYTOPuzmFjJnryx9HVGcaStCe=",
        "s2": "Dkdpgh4ZKsQB80/Mfvw36XI1R25-WUAlEi7NLboqYTOPuzmFjJnryx9HVGcaStCe=",
        "s3": "ckdp1h4ZKsUB80/Mfvw36XIgR25+WQAlEi7NLboqYTOPuzmFjJnryx9HVGDaStCe",
        "s4": "Dkdpgh2ZmsQB80/MfvV36XI1R45-WUAlEixNLwoqYTOPuzKFjJnry79HbGcaStCe"}

    def __init__(self, ):
        self.chunk = []
        self.size = 0
        self.reg = self.__reg[:]

    @classmethod
    def list_1(cls, random_num=None, a=170, b=85, c=45, ) -> list:
        return cls.random_list(
            random_num,
            a,
            b,
            1,
            2,
            5,
            c & a,
        )

    @classmethod
    def list_2(cls, random_num=None, a=170, b=85, ) -> list:
        return cls.random_list(
            random_num,
            a,
            b,
            1,
            0,
            0,
            0,
        )

    @classmethod
    def list_3(cls, random_num=None, a=170, b=85, ) -> list:
        return cls.random_list(
            random_num,
            a,
            b,
            1,
            0,
            5,
            0,
        )

    @staticmethod
    def random_list(
            a: float = None,
            b=170,
            c=85,
            d=0,
            e=0,
            f=0,
            g=0,
    ) -> list:
        r = a or (random() * 10000)
        v = [
            r,
            int(r) & 255,
            int(r) >> 8,
        ]
        s = v[1] & b | d
        v.append(s)
        s = v[1] & c | e
        v.append(s)
        s = v[2] & b | f
        v.append(s)
        s = v[2] & c | g
        v.append(s)
        return v[-4:]

    @staticmethod
    def from_char_code(*args):
        return "".join(chr(code) for code in args)

    @classmethod
    def generate_string_1(
            cls,
            random_num_1=None,
            random_num_2=None,
            random_num_3=None,
    ):
        return cls.from_char_code(*cls.list_1(random_num_1)) + cls.from_char_code(
            *cls.list_2(random_num_2)) + cls.from_char_code(*cls.list_3(random_num_3))

    def generate_string_2(
            self,
            url_params: str,
            user_agent: str,
            start_time=0,
            end_time=0,
    ) -> str:
        a = self.generate_string_2_list(
            url_params,
            user_agent,
            start_time,
            end_time,
        )
        e = self.end_check_num(a)
        a.extend(self.__env)
        a.append(e)
        return self.rc4_encrypt(self.from_char_code(*a), "y")

    def generate_string_2_list(
            self,
            url_params: str,
            user_agent: str,
            start_time=0,
            end_time=0,
    ) -> list:
        start_time = start_time or int(time() * 1000)
        end_time = end_time or (start_time + randint(4, 8))
        params_array = self.sum(self.sum(url_params))
        # TODO: 需要编写一个函数来生成ua_code 2024年6月13日17:13:08
        # Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/90.0.4430.212 Safari/537.36
        ua_code = [76, 98, 15, 131, 97, 245, 224, 133, 122, 199, 241, 166, 79, 34, 90, 191, 128, 126, 122, 98, 66, 11, 14, 40, 49, 110, 110, 173, 67, 96, 138, 252]
        return self.list_4(
            (end_time >> 24) & 255,
            params_array[21],
            ua_code[23],
            (end_time >> 16) & 255,
            params_array[22],
            ua_code[24],
            (end_time >> 8) & 255,
            (end_time >> 0) & 255,
            (start_time >> 24) & 255,
            (start_time >> 16) & 255,
            (start_time >> 8) & 255,
            (start_time >> 0) & 255,
        )

    @staticmethod
    def reg_to_array(a):
        o = [0] * 32
        for i in range(8):
            c = a[i]
            o[4 * i + 3] = (255 & c)
            c >>= 8
            o[4 * i + 2] = (255 & c)
            c >>= 8
            o[4 * i + 1] = (255 & c)
            c >>= 8
            o[4 * i] = (255 & c)

        return o

    def compress(self, a):
        f = self.generate_f(a)
        i = self.reg[:]
        for o in range(64):
            c = self.de(i[0], 12) + i[4] + self.de(self.pe(o), o)
            c = (c & 0xFFFFFFFF)
            c = self.de(c, 7)
            s = (c ^ self.de(i[0], 12)) & 0xFFFFFFFF

            u = self.he(o, i[0], i[1], i[2])
            u = (u + i[3] + s + f[o + 68]) & 0xFFFFFFFF

            b = self.ve(o, i[4], i[5], i[6])
            b = (b + i[7] + c + f[o]) & 0xFFFFFFFF

            i[3] = i[2]
            i[2] = self.de(i[1], 9)
            i[1] = i[0]
            i[0] = u

            i[7] = i[6]
            i[6] = self.de(i[5], 19)
            i[5] = i[4]
            i[4] = (b ^ self.de(b, 9) ^ self.de(b, 17)) & 0xFFFFFFFF

        for l in range(8):
            self.reg[l] = (self.reg[l] ^ i[l]) & 0xFFFFFFFF

    @classmethod
    def generate_f(cls, e):
        r = [0] * 132

        for t in range(16):
            r[t] = (e[4 * t] << 24) | (e[4 * t + 1] <<
                                       16) | (e[4 * t + 2] << 8) | e[4 * t + 3]
            r[t] &= 0xFFFFFFFF

        for n in range(16, 68):
            a = r[n - 16] ^ r[n - 9] ^ cls.de(r[n - 3], 15)
            a = a ^ cls.de(a, 15) ^ cls.de(a, 23)
            r[n] = (a ^ cls.de(r[n - 13], 7) ^ r[n - 6]) & 0xFFFFFFFF

        for n in range(68, 132):
            r[n] = (r[n - 68] ^ r[n - 64]) & 0xFFFFFFFF

        return r

    @staticmethod
    def pad_array(arr, length=60):
        while len(arr) < length:
            arr.append(0)
        return arr

    def fill(self, length=60):
        size = 8 * self.size
        self.chunk.append(128)
        self.chunk = self.pad_array(self.chunk, length)
        for i in range(4):
            self.chunk.append((size >> 8 * (3 - i)) & 255)

    @staticmethod
    def list_4(
            a: int,
            b: int,
            c: int,
            d: int,
            e: int,
            f: int,
            g: int,
            h: int,
            i: int,
            j: int,
            k: int,
            m: int,
    ) -> list:
        return [
            44,
            a,
            0,
            0,
            0,
            0,
            24,
            b,
            58,
            0,
            c,
            d,
            0,
            24,
            97,
            1,
            0,
            239,
            e,
            51,
            f,
            g,
            0,
            0,
            0,
            0,
            h,
            0,
            0,
            14,
            i,
            j,
            0,
            k,
            m,
            3,
            399,
            1,
            399,
            1,
            64,
            0,
            0,
            0]

    @staticmethod
    def end_check_num(a: list):
        r = 0
        for i in a:
            r ^= i
        return r

    @classmethod
    def decode_string(cls, url_string, ):
        decoded = cls.__filter.sub(cls.replace_func, url_string)
        return decoded

    @staticmethod
    def replace_func(match):
        return chr(int(match.group(1), 16))

    @staticmethod
    def de(e, r):
        r %= 32
        return ((e << r) & 0xFFFFFFFF) | (e >> (32 - r))

    @staticmethod
    def pe(e):
        return 2043430169 if 0 <= e < 16 else 2055708042

    @staticmethod
    def he(e, r, t, n):
        if 0 <= e < 16:
            return (r ^ t ^ n) & 0xFFFFFFFF
        elif 16 <= e < 64:
            return (r & t | r & n | t & n) & 0xFFFFFFFF
        raise ValueError

    @staticmethod
    def ve(e, r, t, n):
        if 0 <= e < 16:
            return (r ^ t ^ n) & 0xFFFFFFFF
        elif 16 <= e < 64:
            return (r & t | ~r & n) & 0xFFFFFFFF
        raise ValueError

    @staticmethod
    def convert_to_char_code(a):
        d = []
        for i in a:
            d.append(ord(i))
        return d

    @staticmethod
    def split_array(arr, chunk_size=64):
        result = []
        for i in range(0, len(arr), chunk_size):
            result.append(arr[i:i + chunk_size])
        return result

    @staticmethod
    def char_code_at(s):
        return [ord(char) for char in s]

    def write(self, e, ):
        if isinstance(e, str):
            e = self.decode_string(e + self.__end_string)
            e = self.char_code_at(e)
        self.size = len(e)
        if len(e) <= 64:
            self.chunk = e
        else:
            chunks = self.split_array(e, 64)
            for i in chunks[:-1]:
                self.compress(i)
            self.chunk = chunks[-1]

    def reset(self, ):
        self.chunk = []
        self.size = 0
        self.reg = self.__reg[:]

    def sum(self, e, length=60):
        self.reset()
        self.write(e)
        self.fill(length)
        self.compress(self.chunk)
        a = self.reg_to_array(self.reg)
        self.reset()
        return a

    @classmethod
    def generate_result_unit(cls, n, s):
        r = ""
        for i, j in zip(range(18, -1, -6), (16515072, 258048, 4032, 63)):
            r += cls.__str[s][(n & j) >> i]
        return r

    @classmethod
    def generate_result_end(cls, s, e="s4"):
        r = ""
        b = ord(s[120]) << 16
        r += cls.__str[e][(b & 16515072) >> 18]
        r += cls.__str[e][(b & 258048) >> 12]
        r += "=="
        return r

    @classmethod
    def generate_result(cls, s, n, e="s4"):
        r = ""
        for i in range(n):
            b = ((ord(s[i * 3]) << 16) | (ord(s[i * 3 + 1]))
                 << 8) | ord(s[i * 3 + 2])
            r += cls.generate_result_unit(b, e)
        return r

    @classmethod
    def generate_args_code(cls):
        a = []
        for j in range(24, -1, -8):
            a.append(cls.__arguments[0] >> j)
        a.append(cls.__arguments[1] / 256)
        a.append(cls.__arguments[1] % 256)
        a.append(cls.__arguments[1] >> 24)
        a.append(cls.__arguments[1] >> 16)
        for j in range(24, -1, -8):
            a.append(cls.__arguments[2] >> j)
        return [int(i) & 255 for i in a]

    @staticmethod
    def rc4_encrypt(plaintext, key):
        s = list(range(256))
        j = 0

        # Key Scheduling Algorithm (KSA)
        for i in range(256):
            j = (j + s[i] + ord(key[i % len(key)])) % 256
            s[i], s[j] = s[j], s[i]

        i = 0
        j = 0
        cipher = []

        # Pseudo-Random Generation Algorithm (PRGA)
        for k in range(len(plaintext)):
            i = (i + 1) % 256
            j = (j + s[i]) % 256
            s[i], s[j] = s[j], s[i]
            t = (s[i] + s[j]) % 256
            cipher.append(chr(s[t] ^ ord(plaintext[k])))

        return ''.join(cipher)

    def get_value(self,
                  url_params: dict,
                  user_agent: str,
                  start_time=0,
                  end_time=0,
                  random_num_1=None,
                  random_num_2=None,
                  random_num_3=None,
                  ) -> str:
        string_1 = self.generate_string_1(
            random_num_1,
            random_num_2,
            random_num_3,
        )
        string_2 = self.generate_string_2(
            urlencode(url_params),
            user_agent,
            start_time,
            end_time,
        )
        string = string_1 + string_2
        return self.generate_result(
            string, 40, "s4") + self.generate_result_end(string, "s4")


if __name__ == "__main__":
    bogus = ABogus()
    USERAGENT = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/90.0.4430.212 Safari/537.36"
    url_str = "https://www.douyin.com/aweme/v1/web/aweme/detail/?device_platform=webapp&aid=6383&channel=channel_pc_web&pc_client_type=1&version_code=190500&version_name=19.5.0&cookie_enabled=true&browser_language=zh-CN&browser_platform=Win32&browser_name=Firefox&browser_online=true&engine_name=Gecko&os_name=Windows&os_version=10&platform=PC&screen_width=1920&screen_height=1080&browser_version=124.0&engine_version=122.0.0.0&cpu_core_num=12&device_memory=8&aweme_id=7345492945006595379"
    # 将url参数转换为字典
    url_params = dict([param.split("=") for param in url_str.split("?")[1].split("&")])
    print(f"URL参数: {url_params}")
    a_bogus = bogus.get_value(url_params, USERAGENT)
    # 使用url编码a_bogus
    a_bogus = quote(a_bogus, safe='')
    print(a_bogus)
    print(USERAGENT)
