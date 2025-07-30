![Language](https://img.shields.io/badge/language-Korean-blue)

# 🎧 fastapi-whisper

Whisper AI와 FastAPI를 활용한 **자림 생성 웹 애플리케이션**입니다.
사용자는 영상/오디오 파일을 업로드하면 자동으로 **한국어 및 영어 자림(SRT)** 을 생성하고, 다운로드할 수 있습니다.

---

## 🚀 주요 기능

* 🎤 Whisper AI 기반 음성 인식 및 자림 생성
* 🌐 FastAPI 기반 REST API + WebSocket 실시간 상태 확인
* 🥵 Celery + Redis로 비동기 작업 분산 처리
* 📁 단일 및 다중 파일 업로드 및 분림 처리 UI 제공
* 📅 생성된 SRT 자림 파일 (한국어/영어) 다운로드 지원
* 🧹 임시 파일 자동 삭제 (`threading.Timer` 사용)

---

## 💪 사용 기술 스테크

| 항목     | 기술 스테크                                 |
| ------ | -------------------------------------- |
| 백업     | FastAPI, Python                        |
| 비동기 작업 | Celery, Redis                          |
| 음성 인식  | OpenAI Whisper (local)                 |
| 프론트엔드  | HTML, JavaScript                       |
| 기타     | ffmpeg, tempfile, threading, WebSocket |

---

## 🖼 화면 예시

<img width="1918" height="1012" alt="Whisper 자림 생성기 UI 예시" src="https://github.com/user-attachments/assets/3a3d248b-d3e0-4501-8629-45499b3e5760" />

---

## 🏁 로컬 실행 방법

### ✅ 1. 처음 실행 (환경 설정)

```bash
# 1. 레포지토리 클론 후
setup.bat
```

> `setup.bat` 작업 내용:
>
> * 가상환경 생성 및 활성화
> * pip 업그레이드 및 패키지 설치 (`requirements.txt` 기반)

---

### ✅ 2. 서비스 실행 (매번 실행)

```bash
start.bat
```

> `start.bat` 작업 내용:
>
> * Redis 서버 실행
> * Celery 워커 실행
> * FastAPI 서버 실행 (8000번 포트)

브라우저에서 [http://127.0.0.1:8000](http://127.0.0.1:8000) 열어서 사용

---

## 🔧 ffmpeg 설치 (Windows)

Whisper를 사용하려면 ffmpeg이 필요합니다.  
다음 단계를 따라 설치하세요:

1. [ffmpeg 다운로드 페이지](https://www.gyan.dev/ffmpeg/builds/) 접속  
2. **`ffmpeg-release-essentials.zip`** 파일 다운로드  
3. 압축 해제 후 `bin` 폴더 경로 복사 (예: `C:\ffmpeg\bin`)  
4. **환경변수 등록 (Path 추가)**  
   - 제어판 > 시스템 > 고급 시스템 설정 > 환경 변수 > 시스템 변수 > Path > 편집 > 경로 추가  
5. 설치 확인: 명령어 프롬프트(터미널)에서 아래 명령어 입력

```bash
ffmpeg -version
```

---

## 📂 프로젝트 구조 요약

```
fastapi-whisper-app/
├── app/
│   ├── main.py           # FastAPI 엔트리 포인트
│   ├── tasks.py          # Celery 작업 정의 (Whisper 모델 호출)
│   ├── utils.py          # SRT 파일 생성 유틸 함수
│   └── worker.py         # Celery 워커 실행 진입점
├── templates/
│   └── index.html        # 프론트엔드 템플릿
├── static/
│   └── style.css         # 프론트 스타일
├── requirements.txt      # 설치할 Python 패키지 목록
├── setup.bat             # ✅ 처일 환경 설정용
├── start.bat             # ✅ 매 실행 시 사용하는 자동 실행기
```

---

## ✅ 개발 및 감사화  \uud3ec인트 (예정)

* ⏱ 예상 처리 시간 표시
* 📊 전체 대기여/진행률 UI 표시
* 🤠 Whisper 모델 크기 선택 옵션 추가
* 🌐 Docker 기본 배포 지원
* 🔒 보안 파일 삭제 및 로그 마스킹 처리
