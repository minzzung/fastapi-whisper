@echo off
title 🎧 Whisper 자막 생성기 실행기
chcp 65001 >nul

echo [✅] 가상환경 활성화 중...
call venv\Scripts\activate

echo [🚀] Redis 서버 실행 중...
start "" cmd /k "redis-server"

timeout /t 2 >nul

echo [📡] Celery 워커 실행 중...
start "" cmd /k "celery -A app.tasks worker --loglevel=info --concurrency=1 --pool=solo"

timeout /t 2 >nul

echo [🌐] FastAPI 서버 실행 중...
start "" cmd /k "uvicorn app.main:app --host 127.0.0.1 --port 8000 --reload"

echo -----------------------------------------
echo 모든 서비스가 실행되었습니다!
echo 👉 http://127.0.0.1:8000 접속하세요.
pause
