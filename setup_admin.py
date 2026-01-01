import pymysql
import os
import sys

# 1. 환경 설정: 컨테이너 내부(db)인지 외부(127.0.0.1)인지 자동 판단
DB_HOST = os.environ.get('DB_HOST', '127.0.0.1')
if DB_HOST == 'db':
    # 컨테이너 내부에서 실행될 때 (docker exec 이용 시)
    DB_PORT = 3306
else:
    # PC 로컬에서 실행될 때
    DB_PORT = int(os.environ.get('DB_PORT', 9911))

DB_USER = os.environ.get('DB_USER', 'root')
DB_PASSWORD = os.environ.get('DB_PASSWORD', 'dldntjd@D79')
DB_NAME = os.environ.get('DB_NAME', 'tool_db')

def setup():
    print(f"--- DB 접속 시도: {DB_HOST}:{DB_PORT} (User: {DB_USER}) ---")
    try:
        conn = pymysql.connect(
            host=DB_HOST, user=DB_USER, password=DB_PASSWORD,
            port=DB_PORT, charset='utf8mb4'
        )
        cursor = conn.cursor()
        
        # 데이터베이스 생성 및 선택
        cursor.execute(f"CREATE DATABASE IF NOT EXISTS {DB_NAME}")
        cursor.execute(f"USE {DB_NAME}")
        
        # 1. 사용자 테이블 (비밀번호 기반 로그인을 위함)
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS users (
                id INT AUTO_INCREMENT PRIMARY KEY,
                username VARCHAR(50) NOT NULL UNIQUE,
                password VARCHAR(255) NOT NULL,
                is_admin TINYINT(1) DEFAULT 0
            )
        """)
        
        # 2. 일일 사용량 로그 테이블
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS usage_logs (
                ip_address VARCHAR(45),
                action_type VARCHAR(20),
                usage_count INT DEFAULT 0,
                last_date DATE,
                PRIMARY KEY (ip_address, action_type)
            )
        """)

        # 3. 상세 활동 로그 테이블
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS activity_logs (
                id INT AUTO_INCREMENT PRIMARY KEY,
                ip_address VARCHAR(45),
                action_type VARCHAR(20),
                details TEXT,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """)
        
        print("\n--- 관리자 비밀번호 설정 ---")
        admin_pw = input("사용할 관리자 비밀번호를 입력하세요: ")
        
        if not admin_pw:
            print("비밀번호가 입력되지 않아 종료합니다.")
            return

        # 관리자 계정 고정 (username='admin') 생성 또는 업데이트
        try:
            cursor.execute("INSERT INTO users (username, password, is_admin) VALUES (%s, %s, 1)", ("admin", admin_pw))
            conn.commit()
            print(f"관리자('admin') 설정이 성공적으로 완료되었습니다.")
        except pymysql.err.IntegrityError:
            cursor.execute("UPDATE users SET password=%s WHERE username=%s", (admin_pw, "admin"))
            conn.commit()
            print("기존 관리자의 비밀번호가 업데이트되었습니다.")
            
        conn.close()
    except Exception as e:
        print(f"\n[오류 발생] DB에 연결할 수 없습니다: {e}")
        print("팁: DB 컨테이너(tool_db)가 실행 중인지 확인하세요.")

if __name__ == "__main__":
    setup()