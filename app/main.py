# =====================
# main.py
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

app = FastAPI()

templates = Jinja2Templates(directory="templates")
app.mount("/static", StaticFiles(directory="static"), name="static")

# CORS 설정
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# 메인 페이지
@app.get("/", response_class=HTMLResponse)
async def root(request: Request):
    return templates.TemplateResponse("index.html", {"request": request})

# 자막 생성 요청
@app.post("/transcribe/")
async def transcribe(
    file: UploadFile = File(...),
    want_ko: bool = Form(True),
    want_en: bool = Form(True),
):
    if not file.filename:
        raise HTTPException(status_code=400, detail="파일이 비어 있습니다.")

    suffix = "." + file.filename.split(".")[-1] if "." in file.filename else ".wav"
    orig_name = file.filename

    # 임시 파일 저장
    try:
        with tempfile.NamedTemporaryFile(delete=False, suffix=suffix) as tmp:
            while True:
                chunk = await file.read(1024 * 1024)
                if not chunk:
                    break
                tmp.write(chunk)
            tmp_path = tmp.name
    except Exception:
        raise HTTPException(status_code=500, detail="파일 저장 실패")

    # Celery 태스크 등록
    try:
        task = transcribe_task.delay(tmp_path, suffix, orig_name, want_ko, want_en)
    except Exception:
        raise HTTPException(status_code=500, detail="작업 요청 실패")

    return {"task_id": task.id}

# 상세 상태 조회
@app.get("/status/{task_id}")
def status(task_id: str):
    res = AsyncResult(task_id, app=celery_app)
    state = res.state  # PENDING, RECEIVED, STARTED, PROGRESS, SUCCESS, FAILURE, REVOKED

    # Celery 내부 단계
    if state in ("PENDING", "RECEIVED", "STARTED"):
        step_map = {"PENDING": 0, "RECEIVED": 1, "STARTED": 2}
        return {"state": state,
                "status": ["대기 중","수신됨","시작됨"][step_map[state]],
                "step": step_map[state]}

    # 사용자 정의 진행(PROGRESS)
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

    # 완료/실패/취소
    if state == "SUCCESS":
        return {"state": state, "status": "완료", "step": 6, "download_ready": True, **res.result}
    if state == "FAILURE":
        info = res.info or {}
        return {"state": state, "status": "실패", "step": -1, "detail": info.get("detail", str(info))}
    if state == "REVOKED":
        return {"state": state, "status": "취소됨", "step": -1}

    return {"state": state, "status": "알 수 없음", "step": -1}

# 워커 내부 확인
@app.get("/inspect/")
def inspect_tasks():
    insp = celery_app.control.inspect()
    return {
        "active": insp.active(),
        "reserved": insp.reserved(),
        "scheduled": insp.scheduled(),
    }

# 개별 취소
@app.post("/abort/{task_id}")
async def abort_task(task_id: str):
    res = AsyncResult(task_id, app=celery_app)
    if res.state == "STARTED":
        res.revoke(terminate=True, signal="SIGKILL")
    else:
        res.revoke()
    celery_app.backend.store_result(task_id, None, states.REVOKED)
    return {"status": "취소됨", "task_id": task_id}

# 전체 초기화
@app.post("/abort_all/")
async def abort_all():
    removed = celery_app.control.purge()
    insp = celery_app.control.inspect()
    active = insp.active() or {}
    for worker, tasks in active.items():
        for t in tasks:
            celery_app.control.revoke(t['id'], terminate=True)
            celery_app.backend.store_result(t['id'], None, states.REVOKED)
    return {"status": "전체 초기화 완료", "removed_from_queue": removed}

# 다운로드
@app.get("/download/{task_id}/{lang}")
def download(task_id: str, lang: str = Path(...)):
    res = AsyncResult(task_id, app=celery_app)
    if res.state != "SUCCESS":
        raise HTTPException(status_code=404, detail="자막 준비되지 않음")

    data = res.result
    srt = data.get(f"srt_path_{lang}")
    orig = data.get("original_filename")
    if not srt or not os.path.exists(srt):
        raise HTTPException(status_code=404, detail="SRT 파일 없음")
    fname = f"{os.path.splitext(orig)[0]}_{lang}.srt"
    return FileResponse(srt, media_type="application/x-subrip", filename=fname)