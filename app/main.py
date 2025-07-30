### main.py

import os
from fastapi import FastAPI, UploadFile, File, Form, HTTPException, Request, Path
from fastapi.responses import FileResponse, HTMLResponse
from fastapi.templating import Jinja2Templates #HTML 렌더링 (Jinja2 템플릿 엔진 사용)
from fastapi.middleware.cors import CORSMiddleware
from app.tasks import transcribe_task, celery_app

app = FastAPI()
templates = Jinja2Templates(directory="templates")

#CORS 허용 설정 (다른 도메인에서 접근 가능하게 함)
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

#메인페이지 (GET/ 호출 시 index.html 반환)
@app.get("/", response_class=HTMLResponse)
async def root(request: Request):
    return templates.TemplateResponse("index.html", {"request": request})

#파일 업로드 및 자막 생성 (POST /transcribe/)
@app.post("/transcribe/")
async def transcribe(
    file: UploadFile = File(...),
    want_ko: bool = Form(True), # 체크박스 옵션 수신
    want_en: bool = Form(True)
):
    content = await file.read()
    suffix = "." + file.filename.split(".")[-1] if "." in file.filename else ".wav"
    original_filename = file.filename

    # 파일이 비어 있는지 확인
    if not content:
        raise HTTPException(status_code=400, detail="파일이 비어 있습니다.")
    # 자막 생성 작업을 비동기로 실행
    task = transcribe_task.delay(content, suffix, original_filename, want_ko, want_en)
    return {"task_id": task.id, "original_filename": original_filename}

# 작업 상태 조회 (GET /status/{task_id})
@app.get("/status/{task_id}")
def status(task_id: str):
    result = celery_app.AsyncResult(task_id)
    if result.state == "PENDING":
        return {"status": "대기 중", "step": 0}
    elif result.state == "PROGRESS":
        info = result.info or {}
        return {
            "status": "진행 중",
            "step": info.get("step", 0),
            "detail": info.get("status", ""),
            "srt_path_ko": info.get("srt_path_ko"),
            "srt_path_en": info.get("srt_path_en"),
            "original_filename": info.get("original_filename")
        }
    elif result.state == "SUCCESS":
        return {"status": "완료", "step": 9, **result.result}
    elif result.state == "FAILURE":
        info = result.info or {}
        return {"status": "실패", "step": info.get("step", -1), "detail": info.get("detail", str(info))}
    return {"status": f"알 수 없음 ({result.state})"}


# 자막 파일 다운로드 (GET /download/{task_id}/{lang})
@app.get("/download/{task_id}/{lang}")
def download(task_id: str, lang: str = Path(...)): # 작업 ID로 결과 조회 / lang은 ko 또는 en
    task_result = celery_app.AsyncResult(task_id) 

    if not task_result or task_result.status != "SUCCESS":
        raise HTTPException(status_code=404, detail="자막이 아직 준비되지 않았거나 작업이 실패했습니다.")
    
    result = task_result.result
    lang = lang.lower()
    
    srt_path = result.get(f"srt_path_{lang}")
    original_filename = result.get("original_filename")

    # 언어가 잘못된 경우 예외 처리
    if not srt_path or not os.path.exists(srt_path):
        raise HTTPException(status_code=404, detail="SRT 파일을 찾을 수 없습니다.")

    # 파일 이름 생성 (원본 파일 이름에 언어 접미사 추가)
    base_filename = os.path.splitext(original_filename)[0]
    download_filename = f"{base_filename}_{lang}.srt"

    # SRT 파일 다운로드 형태로 반환
    return FileResponse(
        path=srt_path,
        media_type="application/x-subrip",
        filename=download_filename,
        headers={"Content-Disposition": f'attachment; filename="{download_filename}"'},
    )