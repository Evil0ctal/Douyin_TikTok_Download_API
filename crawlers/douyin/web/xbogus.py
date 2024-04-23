# ==============================================================================
# Copyright (C) 2021 Evil0ctal
#
# This file is part of the Douyin_TikTok_Download_API project.
#
# This project is licensed under the Apache License 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at:
# http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.
# ==============================================================================
# 　　　　 　　  ＿＿
# 　　　 　　 ／＞　　フ
# 　　　 　　| 　_　 _ l
# 　 　　 　／` ミ＿xノ
# 　　 　 /　　　 　 |       Feed me Stars ⭐ ️
# 　　　 /　 ヽ　　 ﾉ
# 　 　 │　　|　|　|
# 　／￣|　　 |　|　|
# 　| (￣ヽ＿_ヽ_)__)
# 　＼二つ
# ==============================================================================
#
# Contributor Link:
# - https://github.com/Evil0ctal
# - https://github.com/Johnserf-Seed
#
# ==============================================================================


import time
import base64
import hashlib


class XBogus:
    def __init__(self, user_agent: str = None) -> None:
        # fmt: off
        self.Array = [
            None, None, None, None, None, None, None, None, None, None, None, None, None, None, None, None,
            None, None, None, None, None, None, None, None, None, None, None, None, None, None, None, None,
            None, None, None, None, None, None, None, None, None, None, None, None, None, None, None, None,
            0, 1, 2, 3, 4, 5, 6, 7, 8, 9, None, None, None, None, None, None, None, None, None, None, None,
            None, None, None, None, None, None, None, None, None, None, None, None, None, None, None, None,
            None, None, None, None, None, None, None, None, None, None, None, None, 10, 11, 12, 13, 14, 15
        ]
        self.character = "Dkdpgh4ZKsQB80/Mfvw36XI1R25-WUAlEi7NLboqYTOPuzmFjJnryx9HVGcaStCe="
        # fmt: on
        self.ua_key = b"\x00\x01\x0c"
        self.user_agent = (
            user_agent
            if user_agent is not None and user_agent != ""
            else "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36 Edg/122.0.0.0"
        )

    def md5_str_to_array(self, md5_str):
        """
        将字符串使用md5哈希算法转换为整数数组。
        Convert a string to an array of integers using the md5 hashing algorithm.
        """
        if isinstance(md5_str, str) and len(md5_str) > 32:
            return [ord(char) for char in md5_str]
        else:
            array = []
            idx = 0
            while idx < len(md5_str):
                array.append(
                    (self.Array[ord(md5_str[idx])] << 4)
                    | self.Array[ord(md5_str[idx + 1])]
                )
                idx += 2
            return array

    def md5_encrypt(self, url_path):
        """
        使用多轮md5哈希算法对URL路径进行加密。
        Encrypt the URL path using multiple rounds of md5 hashing.
        """
        hashed_url_path = self.md5_str_to_array(
            self.md5(self.md5_str_to_array(self.md5(url_path)))
        )
        return hashed_url_path

    def md5(self, input_data):
        """
        计算输入数据的md5哈希值。
        Calculate the md5 hash value of the input data.
        """
        if isinstance(input_data, str):
            array = self.md5_str_to_array(input_data)
        elif isinstance(input_data, list):
            array = input_data
        else:
            raise ValueError("Invalid input type. Expected str or list.")

        md5_hash = hashlib.md5()
        md5_hash.update(bytes(array))
        return md5_hash.hexdigest()

    def encoding_conversion(
        self, a, b, c, e, d, t, f, r, n, o, i, _, x, u, s, l, v, h, p
    ):
        """
        第一次编码转换。
        Perform encoding conversion.
        """
        y = [a]
        y.append(int(i))
        y.extend([b, _, c, x, e, u, d, s, t, l, f, v, r, h, n, p, o])
        re = bytes(y).decode("ISO-8859-1")
        return re

    def encoding_conversion2(self, a, b, c):
        """
        第二次编码转换。
        Perform an encoding conversion on the given input values and return the result.
        """
        return chr(a) + chr(b) + c

    def rc4_encrypt(self, key, data):
        """
        使用RC4算法对数据进行加密。
        Encrypt data using the RC4 algorithm.
        """
        S = list(range(256))
        j = 0
        encrypted_data = bytearray()

        # 初始化 S 盒
        # Initialize the S box
        for i in range(256):
            j = (j + S[i] + key[i % len(key)]) % 256
            S[i], S[j] = S[j], S[i]

        # 生成密文
        # Generate the ciphertext
        i = j = 0
        for byte in data:
            i = (i + 1) % 256
            j = (j + S[i]) % 256
            S[i], S[j] = S[j], S[i]
            encrypted_byte = byte ^ S[(S[i] + S[j]) % 256]
            encrypted_data.append(encrypted_byte)

        return encrypted_data

    def calculation(self, a1, a2, a3):
        """
        对给定的输入值执行位运算计算，并返回结果。
        Perform a calculation using bitwise operations on the given input values and return the result.
        """
        x1 = (a1 & 255) << 16
        x2 = (a2 & 255) << 8
        x3 = x1 | x2 | a3
        return (
            self.character[(x3 & 16515072) >> 18]
            + self.character[(x3 & 258048) >> 12]
            + self.character[(x3 & 4032) >> 6]
            + self.character[x3 & 63]
        )

    def getXBogus(self, url_path):
        """
        获取 X-Bogus 值。
        Get the X-Bogus value.
        """

        array1 = self.md5_str_to_array(
            self.md5(
                base64.b64encode(
                    self.rc4_encrypt(self.ua_key, self.user_agent.encode("ISO-8859-1"))
                ).decode("ISO-8859-1")
            )
        )

        array2 = self.md5_str_to_array(
            self.md5(self.md5_str_to_array("d41d8cd98f00b204e9800998ecf8427e"))
        )
        url_path_array = self.md5_encrypt(url_path)

        timer = int(time.time())
        ct = 536919696
        array3 = []
        array4 = []
        xb_ = ""
        # fmt: off
        new_array = [
            64, 0.00390625, 1, 12,
            url_path_array[14], url_path_array[15], array2[14], array2[15], array1[14], array1[15],
            timer >> 24 & 255, timer >> 16 & 255, timer >> 8 & 255, timer & 255,
            ct >> 24 & 255, ct >> 16 & 255, ct >> 8 & 255, ct & 255
        ]
        # fmt: on
        xor_result = new_array[0]
        for i in range(1, len(new_array)):
            b = new_array[i]
            if isinstance(b, float):
                b = int(b)
            xor_result ^= b

        new_array.append(xor_result)

        idx = 0
        while idx < len(new_array):
            array3.append(new_array[idx])
            try:
                array4.append(new_array[idx + 1])
            except IndexError:
                pass
            idx += 2

        merge_array = array3 + array4

        garbled_code = self.encoding_conversion2(
            2,
            255,
            self.rc4_encrypt(
                "ÿ".encode("ISO-8859-1"),
                self.encoding_conversion(*merge_array).encode("ISO-8859-1"),
            ).decode("ISO-8859-1"),
        )

        idx = 0
        while idx < len(garbled_code):
            xb_ += self.calculation(
                ord(garbled_code[idx]),
                ord(garbled_code[idx + 1]),
                ord(garbled_code[idx + 2]),
            )
            idx += 3
        self.params = "%s&X-Bogus=%s" % (url_path, xb_)
        self.xb = xb_
        return (self.params, self.xb, self.user_agent)


if __name__ == "__main__":
    url_path = "https://www.douyin.com/aweme/v1/web/aweme/post/?device_platform=webapp&aid=6383&channel=channel_pc_web&sec_user_id=MS4wLjABAAAAW9FWcqS7RdQAWPd2AA5fL_ilmqsIFUCQ_Iym6Yh9_cUa6ZRqVLjVQSUjlHrfXY1Y&max_cursor=0&locate_query=false&show_live_replay_strategy=1&need_time_list=1&time_list_query=0&whale_cut_token=&cut_version=1&count=18&publish_video_strategy_type=2&pc_client_type=1&version_code=170400&version_name=17.4.0&cookie_enabled=true&screen_width=1920&screen_height=1080&browser_language=zh-CN&browser_platform=Win32&browser_name=Edge&browser_version=122.0.0.0&browser_online=true&engine_name=Blink&engine_version=122.0.0.0&os_name=Windows&os_version=10&cpu_core_num=12&device_memory=8&platform=PC&downlink=10&effective_type=4g&round_trip_time=50&webid=7335414539335222835&msToken=p9Y7fUBuq9DKvAuN27Peml6JbaMqG2ZcXfFiyDv1jcHrCN00uidYqUgSuLsKl1onC-E_n82m-aKKYE0QGEmxIWZx9iueQ6WLbvzPfqnMk4GBAlQIHcDzxb38FLXXQxAm"
    # ua = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36 Edg/122.0.0.0"
    ua = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/103.0.0.0 Safari/537.36"

    XB = XBogus(user_agent=ua)
    xbogus = XB.getXBogus(url_path)
    print(f"url: {xbogus[0]}, xbogus:{xbogus[1]}, ua: {xbogus[2]}")