import urllib.parse

def srotl(t, e):
    return (t << e) | (t >> (32 - e))

def tendian(t):
    if isinstance(t, int):
        return (16711935 & srotl(t, 8)) | (4278255360 & srotl(t, 24))
    for e in range(len(t)):
        t[e] = tendian(t[e])
    return t

# 没问题
def tbytes_to_words(t):
    e = []
    r = 0
    for n in range(len(t)):
        if r >> 5 >= len(e):
            e.append(0)
        e[r >> 5] |= t[n] << (24 - r % 32)
        r += 8
    return e

def jbinstring_to_bytes(t):
    e = []
    for n in range(len(t)):
        e.append(ord(t[n]) & 255)
    return e

# 没问题
def estring_to_bytes(t):
    return jbinstring_to_bytes(urllib.parse.unquote(urllib.parse.quote(t)))

def _ff(t, e, n, r, o, i, a):
    # 计算中间值 c
    c = t + ((e & n) | (~e & r)) + (o & 0xFFFFFFFF) + a
    # 将 c 转换为 32 位无符号整数
    c = c & 0xFFFFFFFF
    # 左移和右移操作
    c = (c << i | c >> (32 - i)) & 0xFFFFFFFF
    # 返回结果
    return (c + e) & 0xFFFFFFFF

def _gg(t, e, n, r, o, i, a):
    # 计算中间值 c
    c = t + ((e & r) | (n & ~r)) + (o & 0xFFFFFFFF) + a
    # 将 c 转换为 32 位无符号整数
    c = c & 0xFFFFFFFF
    # 左移和右移操作
    c = (c << i | c >> (32 - i)) & 0xFFFFFFFF
    # 返回结果
    return (c + e) & 0xFFFFFFFF

def _hh(t, e, n, r, o, i, a):
    # 计算中间值 c
    c = t + (e ^ n ^ r) + (o & 0xFFFFFFFF) + a
    # 将 c 转换为 32 位无符号整数
    c = c & 0xFFFFFFFF
    # 左移和右移操作
    c = (c << i | c >> (32 - i)) & 0xFFFFFFFF
    # 返回结果
    return (c + e) & 0xFFFFFFFF

def _ii(t, e, n, r, o, i, a):
    # 计算中间值 c
    c = t + (n ^ (e | ~r)) + (o & 0xFFFFFFFF) + a
    # 将 c 转换为 32 位无符号整数
    c = c & 0xFFFFFFFF
    # 左移和右移操作
    c = (c << i | c >> (32 - i)) & 0xFFFFFFFF
    # 返回结果
    return (c + e) & 0xFFFFFFFF

def o(i, a):
    if isinstance(i, str):
        i = estring_to_bytes(i)
    elif isinstance(i, (list, tuple)):
        i = list(i)
    elif not isinstance(i, (list, bytearray)):
        i = str(i)
    c = tbytes_to_words(i)
    u = 8 * len(i)
    s, l, f, p = 1732584193, -271733879, -1732584194, 271733878

    for d in range(len(c)):
        c[d] = (16711935 & (c[d] << 8 | c[d] >> 24)) | (4278255360 & (c[d] << 24 | c[d] >> 8))

    # 确保列表 c 的长度足够大
    while len(c) <= (14 + ((u + 64 >> 9) << 4)):
        c.append(0)

    c[u >> 5] |= 128 << (u % 32)
    c[14 + ((u + 64 >> 9) << 4)] = u

    h, v, y, m = _ff, _gg, _hh, _ii
    for d in range(0, len(c), 16):
        g, b, w, A = s, l, f, p
        # 确保在访问索引之前扩展列表的长度
        while len(c) <= d + 15:
            c.append(0)
        s = h(s, l, f, p, c[d + 0], 7, -680876936)
        p = h(p, s, l, f, c[d + 1], 12, -389564586)
        f = h(f, p, s, l, c[d + 2], 17, 606105819)
        l = h(l, f, p, s, c[d + 3], 22, -1044525330)
        s = h(s, l, f, p, c[d + 4], 7, -176418897)
        p = h(p, s, l, f, c[d + 5], 12, 1200080426)
        f = h(f, p, s, l, c[d + 6], 17, -1473231341)
        l = h(l, f, p, s, c[d + 7], 22, -45705983)
        s = h(s, l, f, p, c[d + 8], 7, 1770035416)
        p = h(p, s, l, f, c[d + 9], 12, -1958414417)
        f = h(f, p, s, l, c[d + 10], 17, -42063)
        l = h(l, f, p, s, c[d + 11], 22, -1990404162)
        s = h(s, l, f, p, c[d + 12], 7, 1804603682)
        p = h(p, s, l, f, c[d + 13], 12, -40341101)
        f = h(f, p, s, l, c[d + 14], 17, -1502002290)
        s = v(s, l := h(l, f, p, s, c[d + 15], 22, 1236535329), f, p, c[d + 1], 5, -165796510)
        p = v(p, s, l, f, c[d + 6], 9, -1069501632)
        f = v(f, p, s, l, c[d + 11], 14, 643717713)
        l = v(l, f, p, s, c[d + 0], 20, -373897302)
        s = v(s, l, f, p, c[d + 5], 5, -701558691)
        p = v(p, s, l, f, c[d + 10], 9, 38016083)
        f = v(f, p, s, l, c[d + 15], 14, -660478335)
        l = v(l, f, p, s, c[d + 4], 20, -405537848)
        s = v(s, l, f, p, c[d + 9], 5, 568446438)
        p = v(p, s, l, f, c[d + 14], 9, -1019803690)
        f = v(f, p, s, l, c[d + 3], 14, -187363961)
        l = v(l, f, p, s, c[d + 8], 20, 1163531501)
        s = v(s, l, f, p, c[d + 13], 5, -1444681467)
        p = v(p, s, l, f, c[d + 2], 9, -51403784)
        f = v(f, p, s, l, c[d + 7], 14, 1735328473)
        s = y(s, l := v(l, f, p, s, c[d + 12], 20, -1926607734), f, p, c[d + 5], 4, -378558)
        p = y(p, s, l, f, c[d + 8], 11, -2022574463)
        f = y(f, p, s, l, c[d + 11], 16, 1839030562)
        l = y(l, f, p, s, c[d + 14], 23, -35309556)
        s = y(s, l, f, p, c[d + 1], 4, -1530992060)
        p = y(p, s, l, f, c[d + 4], 11, 1272893353)
        f = y(f, p, s, l, c[d + 7], 16, -155497632)
        l = y(l, f, p, s, c[d + 10], 23, -1094730640)
        s = y(s, l, f, p, c[d + 13], 4, 681279174)
        p = y(p, s, l, f, c[d + 0], 11, -358537222)
        f = y(f, p, s, l, c[d + 3], 16, -722521979)
        l = y(l, f, p, s, c[d + 6], 23, 76029189)
        s = y(s, l, f, p, c[d + 9], 4, -640364487)
        p = y(p, s, l, f, c[d + 12], 11, -421815835)
        f = y(f, p, s, l, c[d + 15], 16, 530742520)
        s = m(s, l := y(l, f, p, s, c[d + 2], 23, -995338651), f, p, c[d + 0], 6, -198630844)
        p = m(p, s, l, f, c[d + 7], 10, 1126891415)
        f = m(f, p, s, l, c[d + 14], 15, -1416354905)
        l = m(l, f, p, s, c[d + 5], 21, -57434055)
        s = m(s, l, f, p, c[d + 12], 6, 1700485571)
        p = m(p, s, l, f, c[d + 3], 10, -1894986606)
        f = m(f, p, s, l, c[d + 10], 15, -1051523)
        l = m(l, f, p, s, c[d + 1], 21, -2054922799)
        s = m(s, l, f, p, c[d + 8], 6, 1873313359)
        p = m(p, s, l, f, c[d + 15], 10, -30611744)
        f = m(f, p, s, l, c[d + 6], 15, -1560198380)
        l = m(l, f, p, s, c[d + 13], 21, 1309151649)
        s = m(s, l, f, p, c[d + 4], 6, -145523070)
        p = m(p, s, l, f, c[d + 11], 10, -1120210379)
        f = m(f, p, s, l, c[d + 2], 15, 718787259)
        l = m(l, f, p, s, c[d + 9], 21, -343485551)

        s = (s + g) >> 0 & 0xFFFFFFFF
        l = (l + b) >> 0 & 0xFFFFFFFF
        f = (f + w) >> 0 & 0xFFFFFFFF
        p = (p + A) >> 0 & 0xFFFFFFFF

    return tendian([s, l, f, p])

def twords_to_bytes(t):
    e = []
    for n in range(0, 32 * len(t), 8):
        e.append((t[n >> 5] >> (24 - n % 32)) & 255)
    return e

def tbytes_to_hex(t):
    e = []
    for n in range(len(t)):
        e.append(hex(t[n] >> 4)[2:])
        e.append(hex(t[n] & 15)[2:])
    return ''.join(e)

def get_wrid(e):
    n = None
    i = twords_to_bytes(o(e, n))
    return tbytes_to_hex(i)
