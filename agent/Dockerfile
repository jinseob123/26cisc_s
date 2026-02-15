# 파이썬 3.10 슬림 이미지를 베이스로 사용 - 가벼워도 상관없기 때문에 통신과 관리를 위한 기본적인 라이브러리만 있으면 충분함
FROM python:3.10-slim

# 도커 클라이언트 설치 (호스트 도커와 통신하기 위함)
RUN apt-get update && apt-get install -y \
    docker.io \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# 필요한 파이썬 라이브러리 설치
# openai: vLLM 서버와 통신 / docker: 샌드박스 제어 / pandas: 로그 분석 
RUN pip install --no-cache-dir openai docker pandas

# 소스 코드 복사
COPY . .

# 실험 실행 
CMD ["python", "main.py"]
