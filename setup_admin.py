import pymysql
import os

DB_HOST = os.environ.get('DB_HOST', '127.0.0.1')
DB_PORT = int(os.environ.get('DB_PORT', 9911)) # 
DB_USER = 'root'
DB_PASSWORD = 'dldntjd@D79'
DB_NAME = 'tool_db'

def setup():
    conn = pymysql.connect(
        host=DB_HOST, user=DB_USER, password=DB_PASSWORD,
        port=DB_PORT, charset='utf8mb4'
    )
    cursor = conn.cursor()
    
    # DB 생성
    cursor.execute(f"CREATE DATABASE IF NOT EXISTS {DB_NAME}")
    cursor.execute(f"USE {DB_NAME}")
    
    # 사용자 테이블
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS users (
            id INT AUTO_INCREMENT PRIMARY KEY,
            username VARCHAR(50) NOT NULL UNIQUE,
            password VARCHAR(255) NOT NULL,
            is_admin TINYINT(1) DEFAULT 0
        )
    """)
    
    # 사용량 로그 테이블
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS usage_logs (
            ip_address VARCHAR(45),
            action_type VARCHAR(20),
            usage_count INT DEFAULT 0,
            last_date DATE,
            PRIMARY KEY (ip_address, action_type)
        )
    """)
    
    # 관리자 계정 추가 (이미 있으면 패스)
    admin_id = input("생성할 관리자 ID: ")
    admin_pw = input("생성할 관리자 PW: ")
    
    try:
        cursor.execute("INSERT INTO users (username, password, is_admin) VALUES (%s, %s, 1)", (admin_id, admin_pw))
        conn.commit()
        print(f"관리자 계정({admin_id})이 생성되었습니다.")
    except pymysql.err.IntegrityError:
        print("이미 존재하는 ID입니다. 업데이트합니다.")
        cursor.execute("UPDATE users SET password=%s WHERE username=%s", (admin_pw, admin_id))
        conn.commit()
        
    conn.close()

if __name__ == "__main__":
    print("DB 초기화 및 관리자 설정을 시작합니다...")
    setup()