# -*- coding: utf-8 -*-
"""容器健康检查：能连上且返回 401（未鉴权）或 200 就算健康。

为什么单独写：容器内 `docker HEALTHCHECK` 里塞长命令易错，
且首页需要 Basic 鉴权——401 是"服务正常"的预期响应，不是故障。
"""
import os
import sys
import urllib.error
import urllib.request

port = os.environ.get("FUTU_PORT", "8300")
url = f"http://127.0.0.1:{port}/"
try:
    urllib.request.urlopen(url, timeout=3)
    sys.exit(0)                       # 200
except urllib.error.HTTPError as e:
    sys.exit(0 if e.code == 401 else 1)   # 401 = 服务在，且鉴权生效
except Exception:
    sys.exit(1)                       # 连不上 → 不健康