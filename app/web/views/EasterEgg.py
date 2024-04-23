import numpy as np
import time

import pyfiglet
from pywebio import start_server
from pywebio.output import put_text, clear, put_html


def a():
    H, W = 60, 80
    g = np.random.choice([0, 1], size=(H, W))

    def u():
        n = g.copy()
        for i in range(H):
            for j in range(W):
                t = sum([g[i, (j - 1) % W], g[i, (j + 1) % W], g[(i - 1) % H, j], g[(i + 1) % H, j],
                         g[(i - 1) % H, (j - 1) % W], g[(i - 1) % H, (j + 1) % W], g[(i + 1) % H, (j - 1) % W],
                         g[(i + 1) % H, (j + 1) % W]])
                n[i, j] = 1 if g[i, j] == 0 and t == 3 else 0 if g[i, j] == 1 and (t < 2 or t > 3) else g[i, j]
        return n

    def m(s):
        put_text(pyfiglet.figlet_format(s, font="slant"))

    def c():
        m(''.join([chr(int(c, 2)) for c in
                   ['01000101', '01110110', '01101001', '01101100', '01001111', '01100011', '01110100', '01100001',
                    '01101100', '00001010', '01000111', '01000001', '01001101', '01000101', '00001010', '01001111',
                    '01000110', '00001010', '01001100', '01001001', '01000110', '01000101', '00001010', '00110010',
                    '00110000', '00110010', '00110100']]));
        time.sleep(3)
        for i in range(3, 0, -1): clear(); m(str(i)); time.sleep(1)
        clear()

    def h(g):
        return '<table id="life-grid" style="table-layout: fixed; border-spacing:0;">' + ''.join('<tr>' + ''.join(
            f'<td style="width:10px; height:10px; background:{"black" if c else "white"};"></td>' for c in r) + '</tr>'
                                                                                                 for r in
                                                                                                 g) + '</table>'

    c();
    put_html(h(g))

    def r(g):
        return f"<script>" + ''.join(
            f'document.getElementById("life-grid").rows[{i}].cells[{j}].style.background = "{"black" if g[i, j] else "white"}";'
            for i in range(H) for j in range(W)) + "</script>"

    e = time.time() + 120
    while time.time() < e:
        time.sleep(0.1);
        g = u();
        put_html(r(g))


if __name__ == '__main__':
    # A boring code is ready to run!
    # 原神，启动！
    start_server(a, port=80)
