import os
import tempfile
import asyncio #비동기 처리 루프 제어
from fastapi import FastAPI, UploadFile, File, Form, HTTPException, Request, Path, WebSocket, WebSocketDisconnect
from fastapi.responses import FileResponse, HTMLResponse
from fastapi.templating import Jinja2Templates
from fastapi.middleware.cors import CORSMiddleware #CORS(다른 도메인 요청 허용)를 위한 미들웨어
from fastapi.staticfiles import StaticFiles

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

# 메인 페이지 렌더링
@app.get("/", response_class=HTMLResponse)
async def root(request: Request):
    return templates.TemplateResponse("index.html", {"request": request})

# ✅ 파일 업로드 및 자막 생성 요청 (청크 방식으로 저장)
@app.post("/transcribe/")
async def transcribe(
    file: UploadFile = File(...),
    want_ko: bool = Form(True),
    want_en: bool = Form(True)
):
    print("[INFO] 사용자 요청 수신 완료")  # ✅ 1단계

    suffix = "." + file.filename.split(".")[-1] if "." in file.filename else ".wav"
    original_filename = file.filename

    # 파일명이 비어 있는 경우 에러 처리
    if not file.filename:
        raise HTTPException(status_code=400, detail="파일이 비어 있습니다.")

    # ✅ 파일을 청크(조각) 단위로 임시 파일에 저장
    try:
        with tempfile.NamedTemporaryFile(delete=False, suffix=suffix) as tmp:
            while True:
                chunk = await file.read(1024 * 1024)  # 1MB씩 읽기
                if not chunk:
                    break
                tmp.write(chunk)
            tmp_path = tmp.name

        print(f"[INFO] 임시 파일 저장 완료: {tmp_path}")  # ✅ 2단계

    except Exception as e:
        print(f"[ERROR] 파일 저장 중 예외 발생: {e}")
        raise HTTPException(status_code=500, detail="파일 저장 실패")

    #  Celery 비동기 작업 큐 요청
    try:
        task = transcribe_task.delay(tmp_path, suffix, original_filename, want_ko, want_en)
        print(f"[INFO] Celery 작업 전송 완료: {task.id}")  # ✅ 3단계
    except Exception as e:
        print(f"[ERROR] Celery 작업 전송 실패: {e}")
        raise HTTPException(status_code=500, detail="작업 요청 실패")

    return {"task_id": task.id}

# 작업 상태 확인 API (REST 방식)
# @app.get("/status/{task_id}")
# def status(task_id: str):
#     result = celery_app.AsyncResult(task_id)

#     if result.state == "PENDING":
#         return {"status": "대기 중", "step": 0}
#     elif result.state == "PROGRESS":
#         info = result.info or {}
#         return {
#             "status": "진행 중",
#             "step": info.get("step", 0),
#             "detail": info.get("status", ""),
#             "srt_path_ko": info.get("srt_path_ko"),
#             "srt_path_en": info.get("srt_path_en"),
#             "original_filename": info.get("original_filename")
#         }
#     elif result.state == "SUCCESS":
#         return {"status": "완료", "step": 9, **result.result}
#     elif result.state == "FAILURE":
#         info = result.info
#         if isinstance(info, dict):
#             step = info.get("step", -1)
#             detail = info.get("detail", str(info))
#         else:
#             step = -1
#             detail = str(info)
#         return {"status": "실패", "step": step, "detail": detail}
#     elif result.state == "REVOKED":
#         return {"status": "취소됨", "step": -1, "detail": "사용자에 의해 취소된 작업입니다."}


# ✅ WebSocket 기반 실시간 상태 확인
@app.websocket("/ws/status/{task_id}")
async def websocket_status(websocket: WebSocket, task_id: str):
    await websocket.accept()
    try:
        while True:
            result = celery_app.AsyncResult(task_id)
            state = result.state

            print(f"[WS][{task_id}] 상태: {state}")  # ✅ 콘솔 출력 추가됨

            if state == "SUCCESS":
                meta = result.result or {}  # ✅ None 방지
                print(f"[WS][{task_id}] SUCCESS 결과: {meta}")  # ✅ 디버깅용

                await websocket.send_json({
                    "status": "완료",
                    "step": 9,
                    "download_ready": True,
                    **meta  # ✅ 딕셔너리 병합 (srt_path 등 포함될 수 있음)
                })
                break

            elif state == "FAILURE":
                await websocket.send_json({
                    "status": "실패",
                    "detail": str(result.info)
                })
                break

            elif state == "REVOKED":
                await websocket.send_json({
                    "status": "취소됨",
                    "detail": "작업이 사용자에 의해 취소되었습니다."
                })
                break

            elif state == "PROGRESS":
                info = result.info or {}
                await websocket.send_json({
                    "status": "진행 중",
                    "step": info.get("step", 0),
                    "detail": info.get("status", "")
                })

            else:
                await websocket.send_json({"status": "대기 중"})

            await asyncio.sleep(1)

    except WebSocketDisconnect:
        print(f"WebSocket 연결 종료됨: {task_id}")


# 자막 파일 다운로드
@app.get("/download/{task_id}/{lang}")
def download(task_id: str, lang: str = Path(...)):
    task_result = celery_app.AsyncResult(task_id)
    if not task_result or task_result.status != "SUCCESS":
        raise HTTPException(status_code=404, detail="자막이 아직 준비되지 않았거나 작업이 실패했습니다.")

    result = task_result.result
    lang = lang.lower()
    srt_path = result.get(f"srt_path_{lang}")
    original_filename = result.get("original_filename")

    # ✅ 방어 로직 추가
    if not original_filename:
        raise HTTPException(status_code=500, detail="파일 이름 정보가 없습니다.")

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


# 작업 취소 요청 API
@app.post("/abort/{task_id}")
async def abort_task(task_id: str):
    from app.tasks import abort_registry
    abort_registry.add(task_id)
    return {"status": "중단 요청 완료", "task_id": task_id}
