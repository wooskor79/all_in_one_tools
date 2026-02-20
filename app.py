import os
import uuid
import time
import datetime
import shutil
import pymysql
import pandas as pd
import ffmpeg
import yt_dlp
import zipfile
import re
import redis
import json
import threading
from flask import Flask, render_template, request, jsonify, send_file, after_this_request, Response, session
from dbutils.pooled_db import PooledDB
app.secret_key = 'super_secret_key_wooskor'

# 환경 변수 설정
DB_HOST = os.environ.get('DB_HOST', 'db')
DB_USER = os.environ.get('DB_USER', 'root')
DB_PASSWORD = os.environ.get('DB_PASSWORD', 'dldntjd@D79')
DB_NAME = os.environ.get('DB_NAME', 'tool_db')
TEMP_DIR = "/app/temp"
os.makedirs(TEMP_DIR, exist_ok=True)

db_pool = PooledDB(
    creator=pymysql,
    host=DB_HOST, user=DB_USER, password=DB_PASSWORD, db=DB_NAME, 
    charset='utf8mb4', autocommit=True,
    maxconnections=10, mincached=2, maxcached=5
)

redis_client = redis.Redis(host='redis', port=6379, db=0, decode_responses=True)

def set_progress(task_id, percent, msg):
    redis_client.set(f"prog_{task_id}", json.dumps({"percent": percent, "msg": msg}), ex=3600)

def get_progress(task_id):
    data = redis_client.get(f"prog_{task_id}")
    return json.loads(data) if data else {"percent": 0, "msg": "대기"}

def get_db_conn():
    return db_pool.connection()

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
        "ic": get_remain_count(ip, 'convert_integrated'),
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
    if not session.get('is_admin'): return jsonify({"error": "Unauthorized"}), 403
    conn = get_db_conn()
    conn.cursor().execute("UPDATE usage_logs SET usage_count = 0")
    conn.close()
    return jsonify({"status": "success", "message": "초기화 완료"})

@app.route('/progress/<task_id>')
def progress(task_id):
    def generate():
        while True:
            prog = get_progress(task_id)
            yield f"data: {prog['percent']}|{prog['msg']}\n\n"
            if prog['percent'] >= 100 or "오류" in prog['msg']: break
            time.sleep(0.5)
    return Response(generate(), mimetype='text/event-stream')

@app.route('/download_yt', methods=['POST'])
def download_yt():
    if not check_limit('download'): return jsonify({"error": "일일 제한 초과"}), 429
    url = request.form.get('url'); task_id = request.form.get('task_id')
    session_id = uuid.uuid4().hex
    def update_hook(d):
        if d['status'] == 'downloading':
            p = d.get('_percent_str', '0%').replace('%','')
            try: p = float(p)
            except: p = 0
            set_progress(task_id, p, "다운로드 중...")
    ydl_opts = {
        'format': 'bestvideo[ext=mp4]+bestaudio[ext=m4a]/best[ext=mp4]/best',
        'outtmpl': f'{TEMP_DIR}/{session_id}_%(title)s.%(ext)s',
        'merge_output_format': 'mp4', 'progress_hooks': [update_hook],
        'noplaylist': True, 'quiet': True, 'no_warnings': True
    }
    try:
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            info = ydl.extract_info(url, download=True)
            f_path = ydl.prepare_filename(info)
            if not os.path.exists(f_path):
                base, _ = os.path.splitext(f_path)
                if os.path.exists(base + ".mp4"): f_path = base + ".mp4"
            real_name = os.path.basename(f_path).replace(f"{session_id}_", "")
            session['last_vid_title'] = os.path.splitext(real_name)[0]
            add_activity_log('download', f"URL: {url} | File: {real_name}")
        set_progress(task_id, 100, "완료")
        @after_this_request
        def cleanup(res):
            try:
                if os.path.exists(f_path): os.remove(f_path)
            except: pass
            return res
        return send_file(f_path, as_attachment=True, download_name=real_name)
    except Exception as e:
        set_progress(task_id, 0, f"오류: {str(e)}")
        return jsonify({"error": "다운로드 실패"}), 500

@app.route('/convert_srt', methods=['POST'])
def convert_srt():
    if not check_limit('convert'): return jsonify({"error": "일일 제한 초과"}), 429
    file = request.files.get('file'); custom_name = request.form.get('custom_name')
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
            h, r = divmod(s, 3600); m, s = divmod(r, 60); ms = int((s - int(s)) * 1000)
            return f"{int(h):02d}:{int(m):02d}:{int(s):02d},{ms:03d}"
        df['sec'] = df[time_col].apply(parse_time)
        df['end_sec'] = df['sec'].shift(-1).fillna(df['sec'] + 3.0)
        srt_res = []; sub_type = request.form.get('sub_type', 'dual')
        for i, row in df.iterrows():
            o = str(row.get(orig_col,'')).strip(); t = str(row.get(trans_col,'')).strip()
            txt = f"{o}\n{t}" if sub_type=='dual' and o and t else (t if sub_type=='translation' else o)
            srt_res.append(f"{i+1}\n{to_srt_t(row['sec'])} --> {to_srt_t(row['end_sec'])}\n{txt}\n")
        content = "\n".join(srt_res); out_name = (custom_name if custom_name else os.path.splitext(file.filename)[0]) + ".srt"
        add_activity_log('convert', f"Source: {file.filename} -> Result: {out_name}")
        return jsonify({"filename": out_name, "content": content, "status": "success"})
    except Exception as e: return jsonify({"error": str(e), "status": "fail"}), 500

@app.route('/convert_srt_multi', methods=['POST'])
def convert_srt_multi():
    if not check_limit('convert'): return jsonify({"error": "일일 제한 초과"}), 429
    files = request.files.getlist('files[]')
    sub_type = request.form.get('sub_type', 'dual')
    if not files: return jsonify({"error": "파일 없음"}), 400
    
    zip_buffer = io.BytesIO()
    try:
        with zipfile.ZipFile(zip_buffer, "a", zipfile.ZIP_DEFLATED, False) as zip_file:
            for file in files:
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
                    h, r = divmod(s, 3600); m, s = divmod(r, 60); ms = int((s - int(s)) * 1000)
                    return f"{int(h):02d}:{int(m):02d}:{int(s):02d},{ms:03d}"
                
                df['sec'] = df[time_col].apply(parse_time)
                df['end_sec'] = df['sec'].shift(-1).fillna(df['sec'] + 3.0)
                srt_res = []
                for i, row in df.iterrows():
                    o = str(row.get(orig_col,'')).strip(); t = str(row.get(trans_col,'')).strip()
                    txt = f"{o}\n{t}" if sub_type=='dual' and o and t else (t if sub_type=='translation' else o)
                    srt_res.append(f"{i+1}\n{to_srt_t(row['sec'])} --> {to_srt_t(row['end_sec'])}\n{txt}\n")
                
                content = "\n".join(srt_res)
                out_name = os.path.splitext(file.filename)[0] + ".srt"
                zip_file.writestr(out_name, content)
                add_activity_log('convert', f"Source: {file.filename} (Multi) -> Result: {out_name}")
        
        zip_buffer.seek(0)
        return send_file(zip_buffer, as_attachment=True, download_name="converted_srt.zip", mimetype='application/zip')
    except Exception as e:
        return jsonify({"error": str(e), "status": "fail"}), 500

@app.route('/convert_srt_integrated', methods=['POST'])
def convert_srt_integrated():
    """4번 탭: .smi, .ass, .idx/.sub 멀티 파일 SRT 변환"""
    if not check_limit('convert_integrated'): return jsonify({"error": "일일 제한 초과"}), 429
    files = request.files.getlist('files[]')
    if not files: return jsonify({"error": "파일 없음"}), 400
    
    results = []
    # SMI/ASS -> SRT 변환 핵심 로직 파이썬 이식
    for f in files:
        ext = os.path.splitext(f.filename)[1].lower()
        if ext not in ['.smi', '.ass']:
            continue
            
        try:
            raw = f.read()
            try: text = raw.decode('utf-8')
            except: text = raw.decode('cp949', errors='ignore')
            
            srt_content = ""
            if ext == '.smi':
                # SMI to SRT 심플 파서
                sync_pattern = re.compile(r'<SYNC Start=([0-9]+)(?:[^>]+)?>', re.IGNORECASE)
                text = re.sub(r'<!--.*?-->', '', text, flags=re.DOTALL)
                text = re.sub(r'<P Class=[^>]+>', '', text, flags=re.IGNORECASE)
                lines = text.split('\n')
                
                blocks = []
                current_time = None
                current_text = []
                
                for line in lines:
                    match = sync_pattern.search(line)
                    if match:
                        if current_time is not None and current_text:
                            # 텍스트 태그 정리
                            clean_text = ' '.join(current_text)
                            clean_text = re.sub(r'<br\s*?/?>', '\n', clean_text, flags=re.IGNORECASE)
                            clean_text = re.sub(r'<[^>]+>', '', clean_text)
                            if clean_text.strip() and clean_text.strip() != '&nbsp;':
                                blocks.append({"start": current_time, "text": clean_text.strip()})
                        current_time = int(match.group(1))
                        current_text = [line[match.end():].strip()]
                    elif current_time is not None:
                        current_text.append(line.strip())
                
                # 블록들을 SRT 포맷으로
                def ms_to_srt(ms):
                    s, ms = divmod(ms, 1000)
                    m, s = divmod(s, 60)
                    h, m = divmod(m, 60)
                    return f"{h:02d}:{m:02d}:{s:02d},{ms:03d}"
                
                for i in range(len(blocks)):
                    start = blocks[i]['start']
                    end = blocks[i+1]['start'] if i + 1 < len(blocks) else start + 3000
                    srt_content += f"{i+1}\n{ms_to_srt(start)} --> {ms_to_srt(end)}\n{blocks[i]['text']}\n\n"
                    
            elif ext == '.ass':
                # ASS to SRT 파서 (Dialogue 라인 추출)
                lines = text.split('\n')
                dialogue_pattern = re.compile(r'^Dialogue:\s*[^,]+,([^,]+),([^,]+),[^,]+,[^,]+,[^,]+,[^,]+,[^,]+,[^,]+,(.*)$')
                
                def asstime_to_srt(t_str):
                    parts = t_str.split('.')
                    ms = int(parts[1][:2] + "0") if len(parts) > 1 else 0
                    hms = parts[0].split(':')
                    return f"{int(hms[0]):02d}:{int(hms[1]):02d}:{int(hms[2]):02d},{ms:03d}"
                
                count = 1
                for line in lines:
                    match = dialogue_pattern.match(line.strip())
                    if match:
                        start = asstime_to_srt(match.group(1).strip())
                        end = asstime_to_srt(match.group(2).strip())
                        raw_text = match.group(3).strip()
                        # 각종 태그 제거 {\pos} 등, \N은 줄바꿈으로
                        clean_text = re.sub(r'\{[^\}]+\}', '', raw_text)
                        clean_text = clean_text.replace(r'\N', '\n').replace(r'\n', '\n')
                        srt_content += f"{count}\n{start} --> {end}\n{clean_text}\n\n"
                        count += 1
            
            out_name = os.path.splitext(f.filename)[0] + ".srt"
            results.append({"filename": out_name, "content": srt_content})
        except Exception as e:
            continue
            
    add_activity_log('convert_integrated', f"Count: {len(files)} files")
    return jsonify({"status": "success", "files": results})

@app.route('/merge_video', methods=['POST'])
def merge_video():
    if not check_limit('merge'): return jsonify({"error": "일일 제한 초과"}), 429
    task_id = request.form.get('task_id'); v_file = request.files.get('video'); s_file = request.files.get('subtitle')
    if not v_file or not s_file: return jsonify({"error": "파일 누락"}), 400
    sid = uuid.uuid4().hex
    v_path = os.path.join(TEMP_DIR, f"v_{sid}_{v_file.filename}")
    s_path = os.path.join(TEMP_DIR, f"s_{sid}_{s_file.filename}")
    out_path = os.path.join(TEMP_DIR, f"out_{sid}.mkv")
    v_file.save(v_path); s_file.save(s_path)
    try:
        dur = float(ffmpeg.probe(v_path)['format']['duration'])
        process = (ffmpeg.input(v_path).output(ffmpeg.input(s_path), out_path, **{'c': 'copy', 'c:s': 'srt', 'disposition:s:0': 'default'})
            .global_args('-progress', 'pipe:1', '-nostats').run_async(pipe_stdout=True, pipe_stderr=True))
        while True:
            line = process.stdout.readline().decode('utf-8', errors='ignore')
            if not line: break
            if "out_time_ms" in line:
                ms = int(line.split('=')[1]); p = min(99, int((ms/1000000)/dur*100))
                set_progress(task_id, p, "합치는 중...")
        process.wait(); set_progress(task_id, 100, "완료")
        out_name = os.path.splitext(v_file.filename)[0] + "_sub.mkv"
        add_activity_log('merge', f"Video: {v_file.filename}")
        @after_this_request
        def cleanup(r):
            for p in [v_path, s_path, out_path]:
                if os.path.exists(p): os.remove(p)
            return r
        return send_file(out_path, as_attachment=True, download_name=out_name)
    except Exception as e:
        set_progress(task_id, 0, "실패")
        return jsonify({"error": str(e)}), 500

if __name__ == '__main__':
    app.run(host='0.0.0.0', port=5000)