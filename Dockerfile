FROM python:3.11-slim

# 시스템 패키지 설치 (ffmpeg 포함)
ENV DEBIAN_FRONTEND=noninteractive
RUN apt-get update && \
    apt-get install -y ffmpeg default-mysql-client && \
    apt-get clean && \
    rm -rf /var/lib/apt/lists/*

# 작업 디렉토리 설정
WORKDIR /app

# 라이브러리 설치
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# 소스 코드 복사
COPY . .

# 임시 폴더 및 업로드 폴더 생성
RUN mkdir -p temp uploads

CMD ["python", "app.py"]