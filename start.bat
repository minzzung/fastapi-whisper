@echo off
echo 가상환경을 생성합니다...
python -m venv venv

echo 가상환경을 활성화합니다...
call venv\Scripts\activate

echo 라이브러리를 설치합니다...
pip install --upgrade pip
pip install -r requirements.txt

echo FastAPI 서버를 시작합니다...
uvicorn app.main:app --host 127.0.0.1 --port 8000 --reload

pause
