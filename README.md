# 🎧 fastapi-whisper

Whisper AI와 FastAPI를 활용한 **자막 생성 웹 애플리케이션**입니다.  
사용자는 영상/오디오 파일을 업로드하면 자동으로 **한글 및 영어 자막(SRT)** 을 생성하고, 다운로드할 수 있습니다.

---

## 🚀 기능

- 🎤 Whisper AI 기반 음성 인식 및 자막 생성
- 🌐 FastAPI 기반 백엔드
- 🧵 Celery + Redis를 통한 비동기 처리
- 📁 다중 파일 업로드 및 병렬 처리 지원
- 📥 생성된 SRT 자막 다운로드 (한글/영어)

---

## 🛠 사용 기술

| 항목        | 기술 스택 |
|-------------|-----------|
| 백엔드      | FastAPI, Python |
| 비동기 작업 | Celery, Redis |
| 음성 인식   | OpenAI Whisper (local) |
| 프론트엔드  | HTML, JavaScript |
| 기타        | ffmpeg, tempfile, threading |

---

## 🖼 화면 예시

> (스크린샷이나 시연 영상이 있다면 여기에 첨부)

---

## 🏁 실행 방법 (로컬 개발)

```bash
# 1. 레포지토리 클론
git clone https://github.com/minzzung/fastapi-whisper.git
cd fastapi-whisper

# 2. 가상환경 설정 및 패키지 설치
python -m venv venv
venv\Scripts\activate        # Windows 기준
pip install -r requirements.txt

# 3. Redis 실행 (별도 설치 필요)
redis-server

# 4. Celery 워커 실행
celery -A tasks worker --loglevel=info --concurrency=4 --pool=solo

# 5. FastAPI 서버 실행
uvicorn main:app --reload


