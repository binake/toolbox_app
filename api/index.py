import os
import sys

# 让 api/ 下能 import 到项目根目录的 app.py
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app import app  # noqa: E402

# Vercel 的 @vercel/python 运行时会自动识别名为 app 的 WSGI 应用
