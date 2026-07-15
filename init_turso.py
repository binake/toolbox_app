#!/usr/bin/env python
# -*- coding: UTF-8 -*-
"""一次性脚本：在 Turso 云端创建 tools 表。

用法：
    1. 复制 .env.example 为 .env，填好 TURSO_DATABASE_URL 与 TURSO_AUTH_TOKEN
    2. 运行：python init_turso.py
"""
import os

from dotenv import load_dotenv
import libsql_client

load_dotenv()

CREATE_SQL = '''
CREATE TABLE IF NOT EXISTS tools (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    title TEXT NOT NULL,
    version TEXT,
    icon TEXT,
    tags TEXT,
    description TEXT,
    release_date TEXT,
    image_url TEXT,
    tool_url TEXT,
    usage_rights TEXT DEFAULT '',
    detail_content TEXT DEFAULT '',
    changelog TEXT DEFAULT '[]'
)
'''


def main():
    url = os.environ['TURSO_DATABASE_URL'].replace('libsql://', 'https://')
    token = os.environ['TURSO_AUTH_TOKEN']
    client = libsql_client.create_client_sync(url=url, auth_token=token)
    try:
        client.execute(CREATE_SQL)
        print('[OK] Turso tools 表创建成功（或已存在）')
    finally:
        client.close()


if __name__ == '__main__':
    main()
