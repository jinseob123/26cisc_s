# trixie 미러 이슈를 피하기 위해 bookworm 기반으로 고정
FROM python:3.10-slim-bookworm

WORKDIR /app

# 필요한 파이썬 라이브러리 설치
# openai: vLLM 서버와 통신 / docker: 샌드박스 제어 / pandas: 로그 분석
# docker.io(apt)는 설치하지 않습니다. docker SDK + host docker.sock 마운트로 제어합니다.
RUN pip install --no-cache-dir openai docker pandas

# 소스 코드 복사
COPY . .

# 실험 실행 
CMD ["python", "main.py"]
