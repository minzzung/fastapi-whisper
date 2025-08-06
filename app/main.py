# =====================
# main.py - FastAPI 백엔드 서버
# - 비동기 파일 업로드 처리
# - Celery를 통한 Whisper 자막 생성 작업 분산 처리
# - 자막 상태 확인 및 다운로드 기능 제공
# =====================

import os
import tempfile
import asyncio
from fastapi import FastAPI, UploadFile, File, Form, HTTPException, Request, Path
from fastapi.responses import FileResponse, HTMLResponse
from fastapi.templating import Jinja2Templates
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from celery.result import AsyncResult
from celery import states
from app.tasks import transcribe_task, celery_app

# FastAPI 애플리케이션 인스턴스 생성
app = FastAPI()

# 템플릿 디렉터리 설정 (Jinja2를 사용한 HTML 렌더링에 필요)
templates = Jinja2Templates(directory="templates")

# 정적 파일(static/js, css 등)을 제공하기 위한 설정
app.mount("/static", StaticFiles(directory="static"), name="static")

# CORS(Cross-Origin Resource Sharing) 설정: 프론트엔드와의 통신 허용
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],            # 모든 도메인에서의 요청 허용
    allow_credentials=True,
    allow_methods=["*"],            # 모든 HTTP 메서드 허용 (GET, POST 등)
    allow_headers=["*"],            # 모든 헤더 허용
)

# 클라이언트의 메인 접근 경로 (HTML 페이지 반환)
@app.get("/", response_class=HTMLResponse)
async def root(request: Request):
    return templates.TemplateResponse("index.html", {"request": request})


# 자막 생성을 위한 비동기 파일 업로드 및 작업 요청 처리
@app.post("/transcribe/")
async def transcribe(
    file: UploadFile = File(...),       # 업로드된 영상/음성 파일
    want_ko: bool = Form(True),         # 한글 자막 요청 여부
    want_en: bool = Form(True),         # 영어 자막 요청 여부
):
    if not file.filename:
        raise HTTPException(status_code=400, detail="파일이 비어 있습니다.")

    # 확장자 추출 (.mp4, .wav 등) — 기본값은 .wav
    suffix = "." + file.filename.split(".")[-1] if "." in file.filename else ".wav"
    orig_name = file.filename  # 원래 파일명 저장

    # 업로드된 파일을 임시 파일로 저장
    try:
        with tempfile.NamedTemporaryFile(delete=False, suffix=suffix) as tmp:
            while True:
                chunk = await file.read(1024 * 1024)  # 1MB 단위로 비동기 읽기
                if not chunk:
                    break
                tmp.write(chunk)  # 임시 파일에 쓰기
            tmp_path = tmp.name  # 저장된 임시 파일 경로
    except Exception:
        raise HTTPException(status_code=500, detail="파일 저장 실패")

    # Celery 작업 큐에 자막 생성 작업 비동기 등록
    try:
        task = transcribe_task.delay(tmp_path, suffix, orig_name, want_ko, want_en)
    except Exception:
        raise HTTPException(status_code=500, detail="작업 요청 실패")

    return {"task_id": task.id}  # 생성된 작업 ID 반환 (상태 확인 시 사용)


# 특정 작업의 상태 조회 (작업 ID 기반)
@app.get("/status/{task_id}")
def status(task_id: str):
    res = AsyncResult(task_id, app=celery_app)
    state = res.state  # 현재 작업 상태 (예: PENDING, STARTED, SUCCESS 등)

    # Celery 기본 상태 처리
    if state in ("PENDING", "RECEIVED", "STARTED"):
        step_map = {"PENDING": 0, "RECEIVED": 1, "STARTED": 2}
        readable = ["대기 중", "수신됨", "시작됨"]
        return {
            "state": state,
            "status": readable[step_map[state]],
            "step": step_map[state],
        }

    # 작업 진행 중일 경우 (progress 단계는 사용자 정의 상태 정보 포함)
    if state == "PROGRESS":
        info = res.info or {}
        return {
            "state": state,
            "status": info.get("status", "진행 중"),
            "step": info.get("step", 3),
            "srt_path_ko": info.get("srt_path_ko"),
            "srt_path_en": info.get("srt_path_en"),
            "original_filename": info.get("original_filename"),
        }

    # 성공적으로 완료된 경우
    if state == "SUCCESS":
        return {
            "state": state,
            "status": "완료",
            "step": 6,
            "download_ready": True,
            **res.result
        }

    # 실패한 경우
    if state == "FAILURE":
        info = res.info or {}
        return {
            "state": state,
            "status": "실패",
            "step": -1,
            "detail": info.get("detail", str(info)),
        }

    # 작업이 취소된 경우
    if state == "REVOKED":
        return {
            "state": state,
            "status": "취소됨",
            "step": -1,
        }

    # 알 수 없는 상태일 경우
    return {"state": state, "status": "알 수 없음", "step": -1}


# 현재 Celery 워커에서 처리 중인 작업 목록 확인 (디버깅용)
@app.get("/inspect/")
def inspect_tasks():
    insp = celery_app.control.inspect()
    return {
        "active": insp.active(),        # 현재 실행 중인 작업
        "reserved": insp.reserved(),    # 큐에 대기 중인 작업
        "scheduled": insp.scheduled(),  # 예약된 작업
    }


# 단일 작업 강제 취소 (REVOKE)
@app.post("/abort/{task_id}")
async def abort_task(task_id: str):
    res = AsyncResult(task_id, app=celery_app)
    if res.state == "STARTED":
        res.revoke(terminate=True, signal="SIGKILL")  # 강제 종료
    else:
        res.revoke()  # 큐에서만 제거
    celery_app.backend.store_result(task_id, None, states.REVOKED)
    return {"status": "취소됨", "task_id": task_id}


# 모든 대기 중/진행 중 작업 강제 초기화
@app.post("/abort_all/")
async def abort_all():
    removed = celery_app.control.purge()  # 대기 중인 작업 큐 비우기
    insp = celery_app.control.inspect()
    active = insp.active() or {}
    for worker, tasks in active.items():
        for t in tasks:
            celery_app.control.revoke(t['id'], terminate=True)
            celery_app.backend.store_result(t['id'], None, states.REVOKED)
    return {
        "status": "전체 초기화 완료",
        "removed_from_queue": removed
    }


# 작업 완료 후 자막(SRT) 파일 다운로드
@app.get("/download/{task_id}/{lang}")
def download(task_id: str, lang: str = Path(...)):
    res = AsyncResult(task_id, app=celery_app)

    # 작업이 아직 성공적으로 끝나지 않았으면 다운로드 불가
    if res.state != "SUCCESS":
        raise HTTPException(status_code=404, detail="자막 준비되지 않음")

    data = res.result
    srt = data.get(f"srt_path_{lang}")         # 요청한 언어의 자막 경로
    orig = data.get("original_filename")       # 원본 파일명

    if not srt or not os.path.exists(srt):
        raise HTTPException(status_code=404, detail="SRT 파일 없음")

    # 다운로드 파일명: 원본이름_lang.srt
    fname = f"{os.path.splitext(orig)[0]}_{lang}.srt"
    return FileResponse(srt, media_type="application/x-subrip", filename=fname)
