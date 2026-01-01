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

# 환경 변수
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
    """관리자 전용 로그 확인 페이지"""
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
    """관리자용 로그 데이터 API"""
    if not session.get('is_admin'): return jsonify({"error": "Unauthorized"}), 403
    
    conn = get_db_conn()
    cur = conn.cursor(pymysql.cursors.DictCursor)
    cur.execute("SELECT * FROM activity_logs ORDER BY created_at DESC LIMIT 500")
    logs = cur.fetchall()
    conn.close()
    
    # datetime 객체 문자열 변환
    for log in logs:
        log['created_at'] = log['created_at'].strftime('%Y-%m-%d %H:%M:%S')
        
    return jsonify(logs)

@app.route('/api/login', methods=['POST'])
def login():
    data = request.json
    conn = get_db_conn()
    cur = conn.cursor()
    cur.execute("SELECT id, is_admin FROM users WHERE username=%s AND password=%s", (data.get('username'), data.get('password')))
    user = cur.fetchone()
    conn.close()
    if user:
        session['user_id'] = user[0]
        session['is_admin'] = bool(user[1])
        return jsonify({"status": "success"})
    return jsonify({"status": "fail", "message": "로그인 실패"}), 401

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

    ydl_opts = {
        'format': 'bestvideo+bestaudio/best',
        'outtmpl': f'{TEMP_DIR}/{session_id}_%(title)s.%(ext)s',
        'merge_output_format': 'mp4',
        'progress_hooks': [update_hook],
        'restrictfilenames': True 
    }
    
    try:
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            info = ydl.extract_info(url, download=True)
            f_path = ydl.prepare_filename(info)
            base, _ = os.path.splitext(f_path)
            if not os.path.exists(f_path): f_path = base + ".mp4"
            
            real_name = os.path.basename(f_path).replace(f"{session_id}_", "")
            session['last_vid_title'] = os.path.splitext(real_name)[0]
            
            # 로그 저장
            add_activity_log('download', f"URL: {url} | File: {real_name}")

        progress_store[task_id] = {"percent": 100, "msg": "완료"}
        
        @after_this_request
        def cleanup(res):
            try: os.remove(f_path)
            except: pass
            return res
            
        return send_file(f_path, as_attachment=True, download_name=real_name)

    except Exception as e:
        progress_store[task_id] = {"percent": 0, "msg": f"오류: {str(e)}"}
        return jsonify({"error": str(e)}), 500

@app.route('/convert_srt', methods=['POST'])
def convert_srt():
    if not check_limit('convert'): return jsonify({"error": "일일 제한(50회) 초과"}), 429

    file = request.files.get('file')
    custom_name = request.form.get('custom_name')
    
    if not file: return jsonify({"error": "파일 없음"}), 400
    
    try:
        df = pd.read_excel(file, engine='openpyxl')
        
        time_col = next((c for c in df.columns if 'Time' in str(c) or 'Start' in str(c)), "Time")
        orig_col = next((c for c in df.columns if 'Subtitle' in str(c) or 'Original' in str(c)), "")
        trans_col = next((c for c in df.columns if 'Translation' in str(c) or '한국어' in str(c)), "")

        def parse_time(t):
            t = str(t).strip()
            if 's' in t: return float(t.replace('s',''))
            parts = t.split(':')
            if len(parts)==3: return int(parts[0])*3600 + int(parts[1])*60 + float(parts[2])
            if len(parts)==2: return int(parts[0])*60 + float(parts[1])
            return 0.0

        def to_srt_t(s):
            h, r = divmod(s, 3600); m, s = divmod(r, 60)
            ms = int((s - int(s)) * 1000)
            return f"{int(h):02d}:{int(m):02d}:{int(s):02d},{ms:03d}"

        df['sec'] = df[time_col].apply(parse_time)
        df['end_sec'] = df['sec'].shift(-1).fillna(df['sec'] + 3.0)
        
        srt_res = []
        sub_type = request.form.get('sub_type', 'dual')
        
        for i, row in df.iterrows():
            o = str(row.get(orig_col,'')).strip()
            t = str(row.get(trans_col,'')).strip()
            if o == 'nan': o = ''
            if t == 'nan': t = ''
            txt = f"{o}\n{t}" if sub_type=='dual' and o and t else (t if sub_type=='translation' else o)
            srt_res.append(f"{i+1}\n{to_srt_t(row['sec'])} --> {to_srt_t(row['end_sec'])}\n{txt}\n")
            
        content = "\n".join(srt_res)
        out_name = (custom_name if custom_name else os.path.splitext(file.filename)[0]) + ".srt"
        
        # 로그 저장
        add_activity_log('convert', f"Source: {file.filename} -> Result: {out_name}")
        
        return jsonify({"filename": out_name, "content": content, "status": "success"})
        
    except Exception as e:
        return jsonify({"error": str(e), "status": "fail"}), 500

@app.route('/merge_video', methods=['POST'])
def merge_video():
    if not check_limit('merge'): return jsonify({"error": "일일 제한(50회) 초과"}), 429
    
    task_id = request.form.get('task_id')
    v_file = request.files.get('video')
    s_file = request.files.get('subtitle')
    
    if not v_file or not s_file: return jsonify({"error": "파일 누락"}), 400
    
    sid = uuid.uuid4().hex
    v_path = os.path.join(TEMP_DIR, f"v_{sid}_{v_file.filename}")
    s_path = os.path.join(TEMP_DIR, f"s_{sid}_{s_file.filename}")
    out_path = os.path.join(TEMP_DIR, f"out_{sid}.mkv")
    
    v_file.save(v_path)
    s_file.save(s_path)
    
    try:
        dur = float(ffmpeg.probe(v_path)['format']['duration'])
        process = (
            ffmpeg.input(v_path)
            .output(ffmpeg.input(s_path), out_path, **{'c': 'copy', 'c:s': 'srt', 'disposition:s:0': 'default'})
            .global_args('-progress', 'pipe:1', '-nostats')
            .run_async(pipe_stdout=True, pipe_stderr=True)
        )
        while True:
            line = process.stdout.readline().decode('utf-8', errors='ignore')
            if not line: break
            if "out_time_ms" in line:
                ms = int(line.split('=')[1])
                p = min(99, int((ms/1000000)/dur*100))
                progress_store[task_id] = {"percent": p, "msg": "합치는 중..."}
        
        process.wait()
        progress_store[task_id] = {"percent": 100, "msg": "완료"}
        
        out_name = os.path.splitext(v_file.filename)[0] + "_sub.mkv"
        
        # 로그 저장
        add_activity_log('merge', f"Video: {v_file.filename} | Sub: {s_file.filename}")
        
        @after_this_request
        def cleanup(r):
            for p in [v_path, s_path, out_path]:
                if os.path.exists(p): os.remove(p)
            return r
            
        return send_file(out_path, as_attachment=True, download_name=out_name)
    except Exception as e:
        progress_store[task_id] = {"percent": 0, "msg": "실패"}
        return jsonify({"error": str(e)}), 500

if __name__ == '__main__':
    app.run(host='0.0.0.0', port=5000)