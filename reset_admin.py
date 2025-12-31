import pymysql
import os

# 내부 접속용 설정
DB_HOST = 'db'
DB_PORT = 3306 
DB_USER = 'root'
DB_PASSWORD = 'dldntjd@D79'
DB_NAME = 'tool_db'

def reset_admin():
    print("--- 관리자 계정 강제 재설정 ---")
    new_id = input("새 관리자 ID 입력: ")
    new_pw = input("새 관리자 PW 입력: ")

    try:
        conn = pymysql.connect(
            host=DB_HOST, user=DB_USER, password=DB_PASSWORD,
            port=DB_PORT, charset='utf8mb4'
        )
        cursor = conn.cursor()
        
        cursor.execute(f"CREATE DATABASE IF NOT EXISTS {DB_NAME}")
        cursor.execute(f"USE {DB_NAME}")
        
        # 기존 관리자 삭제 후 재생성 (가장 확실한 방법)
        cursor.execute("DELETE FROM users WHERE is_admin = 1")
        cursor.execute("INSERT INTO users (username, password, is_admin) VALUES (%s, %s, 1)", (new_id, new_pw))
        
        conn.commit()
        conn.close()
        print(f"\n[성공] 관리자 계정이 '{new_id}'로 재설정되었습니다.")
        print("이제 웹페이지에서 로그인하세요.")
        
    except Exception as e:
        print(f"\n[오류] DB 접속 실패: {e}")
        print("도커 컨테이너 내부에서 실행 중인지 확인하세요.")

if __name__ == "__main__":
    reset_admin()