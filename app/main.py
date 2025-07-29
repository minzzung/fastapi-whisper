
### main.py
import os
from fastapi import FastAPI, UploadFile, File, HTTPException, Request, Path
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse
from fastapi.templating import Jinja2Templates
from fastapi.middleware.cors import CORSMiddleware
from app.tasks import transcribe_task, celery_app

app = FastAPI()
templates = Jinja2Templates(directory="templates")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

@app.get("/", response_class=HTMLResponse)
async def root(request: Request):
    return templates.TemplateResponse("index.html", {"request": request})

@app.post("/transcribe/")
async def transcribe(file: UploadFile = File(...)):
    content = await file.read()
    suffix = "." + file.filename.split(".")[-1] if "." in file.filename else ".wav"
    original_filename = file.filename

    if not content:
        raise HTTPException(status_code=400, detail="파일이 비어 있습니다.")

    task = transcribe_task.delay(content, suffix, original_filename)
    return {"task_id": task.id, "original_filename": original_filename}

@app.get("/status/{task_id}")
def status(task_id: str):
    result = celery_app.AsyncResult(task_id)

    if result.state == "PENDING":
        return {"status": "대기 중"}
    elif result.state == "PROGRESS":
        return {"status": result.info.get("status", "진행 중")}
    elif result.state == "SUCCESS":
        return {"status": "완료", **result.result}
    elif result.state == "FAILURE":
        return {"status": "실패", "detail": str(result.info)}
    else:
        return {"status": f"알 수 없음 ({result.state})"}

@app.get("/download/{task_id}/{lang}")
def download(task_id: str, lang: str = Path(...)):
    task_result = celery_app.AsyncResult(task_id)

    if not task_result or task_result.status != "SUCCESS":
        raise HTTPException(status_code=404, detail="자막이 아직 준비되지 않았거나 작업이 실패했습니다.")

    result = task_result.result
    lang = lang.lower()

    srt_path = result.get(f"srt_path_{lang}")
    original_filename = result.get("original_filename")

    if not srt_path or not os.path.exists(srt_path):
        raise HTTPException(status_code=404, detail="SRT 파일을 찾을 수 없습니다.")

    base_filename = os.path.splitext(original_filename)[0]
    download_filename = f"{base_filename}_{lang}.srt"

    return FileResponse(
        path=srt_path,
        media_type="application/x-subrip",
        filename=download_filename,
        headers={"Content-Disposition": f'attachment; filename="{download_filename}"'},
    )