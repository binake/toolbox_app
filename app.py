#!/usr/bin/env python
# -*- coding: UTF-8 -*-
"""Star Toolbox - 工具管理后台 (Vercel + Turso + Cloudinary)"""
import os
import io
import uuid
from functools import wraps

from flask import Flask, render_template, request, redirect, url_for, session
from PIL import Image, ImageOps
from dotenv import load_dotenv
import libsql_client
import cloudinary
import cloudinary.uploader

load_dotenv()

app = Flask(__name__)
# session 加密密钥（生产环境通过环境变量注入随机复杂字符串）
app.secret_key = os.environ.get('SECRET_KEY', 'dev-insecure-key-change-me')
# Vercel Hobby 单请求体上限 ~4.5MB，这里限制为 4MB
app.config['MAX_CONTENT_LENGTH'] = 4 * 1024 * 1024

# 后台登录凭据（仅从环境变量读取，源码中不保存任何默认值）
ADMIN_USERNAME = os.environ.get('ADMIN_USERNAME')
ADMIN_PASSWORD = os.environ.get('ADMIN_PASSWORD')

ALLOWED_EXTENSIONS = {'png', 'jpg', 'jpeg', 'gif', 'webp', 'svg', 'bmp'}

# 图片压缩配置
MAX_IMAGE_DIM = 1600   # 大图最长边限制（像素）
MAX_ICON_DIM = 256     # 图标最长边限制（像素）
JPG_QUALITY = 82       # 有损 JPG 压缩质量

# Cloudinary 配置（自动读取 CLOUDINARY_URL 环境变量）
cloudinary.config(secure=True)


# ---------- 登录校验 ----------
def login_required(view_func):
    """装饰器：未登录访问后台时跳转到登录页"""
    @wraps(view_func)
    def wrapper(*args, **kwargs):
        if not session.get('logged_in'):
            return redirect(url_for('admin_login', next=request.path))
        return view_func(*args, **kwargs)
    return wrapper


# ---------- Jinja 过滤器 ----------
@app.template_filter('from_json')
def from_json_filter(value):
    import json
    try:
        return json.loads(value) if value else []
    except Exception:
        return []


def allowed_file(filename):
    return '.' in filename and \
        filename.rsplit('.', 1)[1].lower() in ALLOWED_EXTENSIONS


# ---------- Turso 数据库辅助 ----------
def _client():
    """创建 Turso 远程 HTTP 客户端。URL 需为 https:// 形式。"""
    url = os.environ['TURSO_DATABASE_URL'].replace('libsql://', 'https://')
    return libsql_client.create_client_sync(
        url=url, auth_token=os.environ['TURSO_AUTH_TOKEN']
    )


def query_all(sql, params=()):
    """查询多行，返回 list[dict]（模板 tool.xxx 写法无需改动）"""
    c = _client()
    try:
        rs = c.execute(sql, list(params))
        return [dict(zip(rs.columns, row)) for row in rs.rows]
    finally:
        c.close()


def query_one(sql, params=()):
    """查询单行，返回 dict 或 None"""
    rows = query_all(sql, params)
    return rows[0] if rows else None


def execute(sql, params=()):
    """执行写操作（INSERT/UPDATE/DELETE），单语句自动提交"""
    c = _client()
    try:
        c.execute(sql, list(params))
    finally:
        c.close()


# ---------- 图片处理：压缩后上传 Cloudinary ----------
def _process_image(file, prefix='', to_jpg=True, max_dim=MAX_IMAGE_DIM, quality=JPG_QUALITY):
    """压缩图片并上传 Cloudinary，返回可访问的 https URL。
    - to_jpg=True: 缩放并转为有损 JPG（预览大图、详情正文图）
    - to_jpg=False: 缩放但保留透明度，存为 PNG（图标）
    - SVG 为矢量图，原样上传
    压缩失败时回退为原图上传。
    """
    ext = file.filename.rsplit('.', 1)[1].lower()
    raw = file.read()
    public_id = f"{prefix}{uuid.uuid4().hex}"

    if ext == 'svg':
        res = cloudinary.uploader.upload(
            io.BytesIO(raw), folder='star_toolbox',
            public_id=public_id, resource_type='image'
        )
        return res['secure_url']

    try:
        img = Image.open(io.BytesIO(raw))
        img = ImageOps.exif_transpose(img)  # 修正手机照片方向
        img.thumbnail((max_dim, max_dim), Image.LANCZOS)

        buf = io.BytesIO()
        if to_jpg:
            # 透明背景填白后转 RGB
            if img.mode in ('RGBA', 'LA', 'P'):
                bg = Image.new('RGB', img.size, (255, 255, 255))
                rgba = img.convert('RGBA')
                bg.paste(rgba, mask=rgba.split()[-1])
                img = bg
            else:
                img = img.convert('RGB')
            img.save(buf, 'JPEG', quality=quality, optimize=True)
        else:
            img.save(buf, 'PNG', optimize=True)
        buf.seek(0)
        res = cloudinary.uploader.upload(buf, folder='star_toolbox', public_id=public_id)
        return res['secure_url']
    except Exception as e:
        print(f'[_process_image] compress failed: {e}, upload raw')
        res = cloudinary.uploader.upload(io.BytesIO(raw), folder='star_toolbox', public_id=public_id)
        return res['secure_url']


def handle_image_upload(request, existing_id=None):
    """处理预览图上传：有新文件则压缩上传返回 URL，否则保留原值"""
    try:
        if 'image_file' in request.files:
            file = request.files['image_file']
            if file and file.filename and allowed_file(file.filename):
                return _process_image(file, prefix='', to_jpg=True)

        if existing_id is not None:
            row = query_one('SELECT image_url FROM tools WHERE id = ?', (existing_id,))
            if row:
                return row['image_url']

        return request.form.get('image_url', '')
    except Exception as e:
        print(f'[handle_image_upload] Error: {e}')
        return request.form.get('image_url', '')


def handle_icon_upload(request, existing_id=None):
    """处理图标上传：支持文件上传、图片URL、Emoji"""
    try:
        if 'icon_file' in request.files:
            file = request.files['icon_file']
            if file and file.filename and allowed_file(file.filename):
                return _process_image(file, prefix='icon_', to_jpg=False, max_dim=MAX_ICON_DIM)

        icon_text = request.form.get('icon', '').strip()
        if icon_text:
            return icon_text

        if existing_id is not None:
            row = query_one('SELECT icon FROM tools WHERE id = ?', (existing_id,))
            if row:
                return row['icon']

        return ''
    except Exception as e:
        print(f'[handle_icon_upload] Error: {e}')
        return request.form.get('icon', '')


# ---------- 前台路由 ----------
@app.route('/')
def index():
    try:
        tools = query_all('SELECT * FROM tools ORDER BY release_date DESC')
    except Exception as e:
        print(f'[index] DB Error: {e}')
        tools = []
    return render_template('index.html', tools=tools)


@app.route('/tool/<int:id>')
def tool_detail(id):
    try:
        tool = query_one('SELECT * FROM tools WHERE id = ?', (id,))
        if tool is None:
            return "工具未找到", 404
    except Exception as e:
        print(f'[tool_detail] DB Error: {e}')
        return "数据库错误", 500
    return render_template('tool_detail.html', tool=tool)


# ---------- 后台：富文本图片上传 ----------
@app.route('/admin/upload_image', methods=['POST'])
@login_required
def admin_upload_image():
    """富文本详情编辑器图片上传：压缩上传并返回可访问 URL"""
    try:
        if 'image_file' not in request.files:
            return {'error': '未接收到文件'}, 400
        file = request.files['image_file']
        if not file or not file.filename:
            return {'error': '文件为空'}, 400
        if not allowed_file(file.filename):
            return {'error': '不支持的图片格式'}, 400
        url = _process_image(file, prefix='content_', to_jpg=True)
        return {'url': url}
    except Exception as e:
        print(f'[admin_upload_image] Error: {e}')
        return {'error': str(e)}, 500


# ---------- 后台：登录 / 登出 ----------
@app.route('/admin/login', methods=['GET', 'POST'])
def admin_login():
    if session.get('logged_in'):
        return redirect(url_for('admin_dashboard'))
    error = None
    if request.method == 'POST':
        # 未配置环境变量时拒绝登录，避免空凭据误匹配
        if not ADMIN_USERNAME or not ADMIN_PASSWORD:
            return render_template('login.html', error='后台未配置登录凭据，请设置 ADMIN_USERNAME / ADMIN_PASSWORD 环境变量')
        username = request.form.get('username', '').strip()
        password = request.form.get('password', '')
        if username == ADMIN_USERNAME and password == ADMIN_PASSWORD:
            session['logged_in'] = True
            session['username'] = username
            next_url = request.args.get('next') or url_for('admin_dashboard')
            return redirect(next_url)
        error = '用户名或密码错误'
    return render_template('login.html', error=error)


@app.route('/admin/logout')
def admin_logout():
    session.clear()
    return redirect(url_for('admin_login'))


# ---------- 后台：管理 ----------
@app.route('/admin')
@login_required
def admin_dashboard():
    try:
        tools = query_all('SELECT * FROM tools ORDER BY release_date DESC')
    except Exception as e:
        print(f'[admin_dashboard] DB Error: {e}')
        tools = []
    return render_template('admin.html', tools=tools)


@app.route('/admin/add', methods=['POST'])
@login_required
def admin_add():
    try:
        image_url = handle_image_upload(request)
        icon = handle_icon_upload(request)
        form = request.form
        changelog = request.form.get('changelog', '[]')
        execute('''
            INSERT INTO tools (title, version, icon, tags, description, release_date, image_url, tool_url, usage_rights, detail_content, changelog)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        ''', (form['title'], form['version'], icon, form['tags'], form['description'],
              form['release_date'], image_url, form['tool_url'], form['usage_rights'],
              form['detail_content'], changelog))
    except Exception as e:
        print(f'[admin_add] Error: {e}')
    return redirect(url_for('admin_dashboard'))


@app.route('/admin/edit/<int:id>', methods=['POST'])
@login_required
def admin_edit(id):
    try:
        image_url = handle_image_upload(request, id)
        icon = handle_icon_upload(request, id)
        form = request.form
        changelog = request.form.get('changelog', '[]')
        execute('''
            UPDATE tools
            SET title=?, version=?, icon=?, tags=?, description=?, release_date=?, image_url=?, tool_url=?, usage_rights=?, detail_content=?, changelog=?
            WHERE id=?
        ''', (form['title'], form['version'], icon, form['tags'], form['description'],
              form['release_date'], image_url, form['tool_url'], form['usage_rights'],
              form['detail_content'], changelog, id))
    except Exception as e:
        print(f'[admin_edit] Error: {e}')
    return redirect(url_for('admin_dashboard'))


@app.route('/admin/dbcheck')
@login_required
def admin_dbcheck():
    """数据库诊断：在浏览器直接查看连接/建表/写入是否正常（调试用，可后续删除）。"""
    import traceback
    report = {}
    # 1) 环境变量是否存在（只显示是否配置，不暴露具体值）
    report['env_configured'] = {
        'TURSO_DATABASE_URL': bool(os.environ.get('TURSO_DATABASE_URL')),
        'TURSO_AUTH_TOKEN': bool(os.environ.get('TURSO_AUTH_TOKEN')),
        'CLOUDINARY_URL': bool(os.environ.get('CLOUDINARY_URL')),
    }
    # 2) 读测试：列出表 + tools 记录数
    try:
        tables = query_all("SELECT name FROM sqlite_master WHERE type='table'")
        report['tables'] = [t['name'] for t in tables]
        report['read_test'] = 'OK'
    except Exception as e:
        report['read_test'] = 'FAILED'
        report['read_error'] = str(e)
        report['traceback'] = traceback.format_exc()
        return report
    # 3) 写测试：插入一条临时记录再删除，能暴露只读令牌/表缺失问题
    try:
        execute("INSERT INTO tools (title, version) VALUES (?, ?)", ('__dbcheck__', 'test'))
        report['tools_count_after_insert'] = query_one('SELECT COUNT(*) AS c FROM tools')['c']
        execute("DELETE FROM tools WHERE title = ?", ('__dbcheck__',))
        report['write_test'] = 'OK'
    except Exception as e:
        report['write_test'] = 'FAILED'
        report['write_error'] = str(e)
        report['traceback'] = traceback.format_exc()
    return report


@app.route('/admin/delete/<int:id>', methods=['POST'])
@login_required
def admin_delete(id):
    try:
        # 纯云端模式：仅删除数据库记录，图片保留在 Cloudinary
        execute('DELETE FROM tools WHERE id = ?', (id,))
    except Exception as e:
        print(f'[admin_delete] Error: {e}')
    return redirect(url_for('admin_dashboard'))


if __name__ == '__main__':
    debug = os.environ.get('FLASK_DEBUG', '1') == '1'
    port = int(os.environ.get('PORT', 5000))
    app.run(host='0.0.0.0', port=port, debug=debug)
