import os
import asyncio
from fastapi import FastAPI, UploadFile, File, Form, HTTPException, Request, Path, WebSocket, WebSocketDisconnect
from fastapi.responses import FileResponse, HTMLResponse
from fastapi.templating import Jinja2Templates #템플릿 렌더링 (index.html용)
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from app.tasks import transcribe_task, celery_app

app = FastAPI()
templates = Jinja2Templates(directory="templates")
app.mount("/static", StaticFiles(directory="static"), name="static")

app.add_middleware(
    CORSMiddleware,   # 모든 출처 허용
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

#메인 페이지 렌더링
@app.get("/", response_class=HTMLResponse)
async def root(request: Request):
    return templates.TemplateResponse("index.html", {"request": request})

#파일 업로드 및 자막 생성 요청
@app.post("/transcribe/")
async def transcribe(
    file: UploadFile = File(...),
    want_ko: bool = Form(True),
    want_en: bool = Form(True)
):
    content = await file.read()
    suffix = "." + file.filename.split(".")[-1] if "." in file.filename else ".wav"
    original_filename = file.filename

    if not content:
        raise HTTPException(status_code=400, detail="파일이 비어 있습니다.")

    #Celery의 비동기 작업 등록 // 서버와 분리된 프로세스에서 작업을 실행 => 비동기작업큐
    task = transcribe_task.delay(content, suffix, original_filename, want_ko, want_en)
    return {"task_id": task.id, "original_filename": original_filename}

# 작업 상태 확인1 REST API 이용 (현재 사용 안함)
@app.get("/status/{task_id}")
def status(task_id: str):
    result = celery_app.AsyncResult(task_id)
    
    #Redis 백엔드에서 task_id로 작업 상태를 조회
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
        info = result.info
        if isinstance(info, dict):
            step = info.get("step", -1)
            detail = info.get("detail", str(info))
        else:
            step = -1
            detail = str(info)
        return {"status": "실패", "step": step, "detail": detail}

# 작업상태확인2 WebSocket 이용 (현재 사용)
@app.websocket("/ws/status/{task_id}")
async def websocket_status(websocket: WebSocket, task_id: str):
    await websocket.accept()
    try:
        #클라이언트와 WebSocket을 연결하여 실시간으로 상태 정보 전송
        while True:
            result = celery_app.AsyncResult(task_id)
            state = result.state

            if state == "SUCCESS":
                await websocket.send_json({"status": "완료", **result.result})
                break
            elif state == "FAILURE":
                await websocket.send_json({"status": "실패", "detail": str(result.info)})
                break
            elif state == "PROGRESS":
                info = result.info or {}
                await websocket.send_json({
                    "status": "진행 중",
                    "step": info.get("step", 0),
                    "detail": info.get("status", "")
                })

            await asyncio.sleep(1)
    except WebSocketDisconnect:
        print(f"WebSocket 연결 종료됨: {task_id}")


#자막 파일 다운로드 
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

