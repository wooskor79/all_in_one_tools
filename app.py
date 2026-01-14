import os
import uuid
import time
import datetime
import shutil
import pymysql
import pandas as pd
import ffmpeg
import yt_dlp
from flask import Flask, render_template, request, jsonify, send_file, after_this_request, Response, session

app = Flask(__name__)
app.secret_key = 'super_secret_key_wooskor'

# 환경 변수 설정
DB_HOST = os.environ.get('DB_HOST', 'db')
DB_USER = os.environ.get('DB_USER', 'root')
DB_PASSWORD = os.environ.get('DB_PASSWORD', 'dldntjd@D79')
DB_NAME = os.environ.get('DB_NAME', 'tool_db')
TEMP_DIR = "/app/temp"
os.makedirs(TEMP_DIR, exist_ok=True)

progress_store = {}

def get_db_conn():
    return pymysql.connect(host=DB_HOST, user=DB_USER, password=DB_PASSWORD, db=DB_NAME, charset='utf8mb4', autocommit=True)

def add_activity_log(action_type, details):
    """상세 활동 로그를 DB에 저장합니다."""
    ip = request.remote_addr
    try:
        conn = get_db_conn()
        cur = conn.cursor()
        cur.execute("INSERT INTO activity_logs (ip_address, action_type, details) VALUES (%s, %s, %s)", 
                    (ip, action_type, details))
        conn.close()
    except Exception as e:
        print(f"Logging Error: {e}")

def get_remain_count(ip, action_type):
    if session.get('is_admin'): return 9999
    conn = get_db_conn()
    cur = conn.cursor()
    today = datetime.date.today()
    cur.execute("SELECT usage_count, last_date FROM usage_logs WHERE ip_address=%s AND action_type=%s", (ip, action_type))
    row = cur.fetchone()
    conn.close()
    
    used = 0
    if row and row[1] == today:
        used = row[0]
    return max(0, 50 - used)

def check_limit(action_type):
    if session.get('is_admin'): return True
    ip = request.remote_addr
    remain = get_remain_count(ip, action_type)
    if remain <= 0: return False
    
    conn = get_db_conn()
    cur = conn.cursor()
    today = datetime.date.today()
    
    cur.execute("SELECT usage_count, last_date FROM usage_logs WHERE ip_address=%s AND action_type=%s", (ip, action_type))
    row = cur.fetchone()
    
    if row:
        if row[1] != today:
            cur.execute("UPDATE usage_logs SET usage_count=1, last_date=%s WHERE ip_address=%s AND action_type=%s", (today, ip, action_type))
        else:
            cur.execute("UPDATE usage_logs SET usage_count = usage_count + 1 WHERE ip_address=%s AND action_type=%s", (ip, action_type))
    else:
        cur.execute("INSERT INTO usage_logs (ip_address, action_type, usage_count, last_date) VALUES (%s, %s, 1, %s)", (ip, action_type, today))
    
    conn.close()
    return True

@app.route('/')
def index():
    return render_template('index.html', is_admin=session.get('is_admin', False))

@app.route('/history')
def history_page():
    if not session.get('is_admin'):
        return "<script>alert('권한이 없습니다.'); location.href='/';</script>"
    return render_template('history.html')

@app.route('/api/status')
def api_status():
    ip = request.remote_addr
    return jsonify({
        "dl": get_remain_count(ip, 'download'),
        "cv": get_remain_count(ip, 'convert'),
        "mg": get_remain_count(ip, 'merge'),
        "last_title": session.get('last_vid_title', None),
        "is_admin": session.get('is_admin', False)
    })

@app.route('/api/admin/logs')
def get_admin_logs():
    if not session.get('is_admin'): return jsonify({"error": "Unauthorized"}), 403
    
    conn = get_db_conn()
    cur = conn.cursor(pymysql.cursors.DictCursor)
    cur.execute("SELECT * FROM activity_logs ORDER BY created_at DESC LIMIT 500")
    logs = cur.fetchall()
    conn.close()
    
    for log in logs:
        log['created_at'] = log['created_at'].strftime('%Y-%m-%d %H:%M:%S')
        
    return jsonify(logs)

@app.route('/api/login', methods=['POST'])
def login():
    data = request.json
    password = data.get('password')
    
    conn = get_db_conn()
    cur = conn.cursor()
    cur.execute("SELECT id, is_admin FROM users WHERE password=%s AND is_admin=1", (password,))
    user = cur.fetchone()
    conn.close()
    
    if user:
        session['user_id'] = user[0]
        session['is_admin'] = bool(user[1])
        return jsonify({"status": "success"})
    return jsonify({"status": "fail", "message": "비밀번호가 일치하지 않습니다."}), 401

@app.route('/api/logout', methods=['POST'])
def logout():
    session.clear()
    return jsonify({"status": "success"})

@app.route('/api/reset_counts', methods=['POST'])
def reset_counts():
    if not session.get('is_admin'): return jsonify({"status": "error"}), 403
    conn = get_db_conn()
    conn.cursor().execute("UPDATE usage_logs SET usage_count = 0")
    conn.close()
    return jsonify({"status": "success", "message": "초기화 완료"})

@app.route('/progress/<task_id>')
def progress(task_id):
    def generate():
        while True:
            prog = progress_store.get(task_id, {"percent": 0, "msg": "대기"})
            yield f"data: {prog['percent']}|{prog['msg']}\n\n"
            if prog['percent'] >= 100 or "오류" in prog['msg']: break
            time.sleep(0.5)
    return Response(generate(), mimetype='text/event-stream')

@app.route('/download_yt', methods=['POST'])
def download_yt():
    """유튜브 다운로드 로직 개선"""
    if not check_limit('download'): return jsonify({"error": "일일 제한(50회) 초과"}), 429
    
    url = request.form.get('url')
    task_id = request.form.get('task_id')
    session_id = uuid.uuid4().hex
    
    def update_hook(d):
        if d['status'] == 'downloading':
            p = d.get('_percent_str', '0%').replace('%','')
            try: p = float(p)
            except: p = 0
            progress_store[task_id] = {"percent": p, "msg": "다운로드 중..."}

    # 옵션 보강: 쿠키 우회 및 경로 최적화
    ydl_opts = {
        'format': 'bestvideo[ext=mp4]+bestaudio[ext=m4a]/best[ext=mp4]/best',
        'outtmpl': f'{TEMP_DIR}/{session_id}_%(title)s.%(ext)s',
        'merge_output_format': 'mp4',
        'progress_hooks': [update_hook],
        'noplaylist': True,
        'quiet': True,
        'no_warnings': True
    }
    
    try:
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            # 1. 정보 추출
            info = ydl.extract_info(url, download=True)
            f_path = ydl.prepare_filename(info)
            
            # 2. 파일 확장자 보정 (병합 시 mp4로 강제되었는지 확인)
            base, ext = os.path.splitext(f_path)
            if not os.path.exists(f_path):
                if os.path.exists(base + ".mp4"):
                    f_path = base + ".mp4"
            
            real_name = os.path.basename(f_path).replace(f"{session_id}_", "")
            session['last_vid_title'] = os.path.splitext(real_name)[0]
            
            add_activity_log('download', f"URL: {url} | File: {real_name}")

        progress_store[task_id] = {"percent": 100, "msg": "완료"}
        
        @after_this_request
        def cleanup(res):
            try:
                if os.path.exists(f_path): os.remove(f_path)
            except: pass
            return res
            
        return send_file(f_path, as_attachment=True, download_name=real_name)

    except Exception as e:
        print(f"yt-dlp Error: {str(e)}") # 서버 로그 확인용
        progress_store[task_id] = {"percent": 0, "msg": f"오류: {str(e)}"}
        return jsonify({"error": "다운로드 실패. URL을 확인하거나 잠시 후 시도하세요."}), 500

# (중략: convert_srt, merge_video 등 다른 함수는 기존과 동일)

if __name__ == '__main__':
    app.run(host='0.0.0.0', port=5000)