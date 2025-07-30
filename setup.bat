@echo off
title Whisper 자막 생성기 환경 설정
chcp 65001 >nul

echo [🔧] 가상환경을 생성합니다...
python -m venv venv

echo [✅] 가상환경을 활성화합니다...
call venv\Scripts\activate

echo [📦] 라이브러리를 설치합니다...
pip install --upgrade pip
pip install -r requirements.txt

echo ---------------------------------------------
echo 🟢 환경 설정 완료! 이제 start.bat로 실행하세요.
pause
