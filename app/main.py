# =====================
# main.py - FastAPI 백엔드 서버 (정리/완성본 + 쉬운 주석 풀버전)
# =====================
#
# 이 파일은 프론트엔드(브라우저)에서 오는 요청을 받아서,
#   1) 파일을 업로드 받고
#   2) Celery 작업 큐에 "자막 생성" 작업을 넣고
#   3) 작업 상태를 조회하고
#   4) 작업을 취소하거나 전체 초기화하고
#   5) 완료된 자막(SRT)을 다운로드하게 해주는
# FastAPI 기반 백엔드 서버입니다.
#
# 핵심 
# - 실제 무거운 자막 생성은 Celery 워커가 처리합니다. (비동기/백그라운드)
# - FastAPI는 얇은 컨트롤러: 큐에 넣고, 상태 물어보고, 취소/초기화만 함
# - Redis는 브로커/백엔드: 작업 상태 저장 + 큐 관리
# - 프론트엔드는 /status/<task_id>를 계속 폴링해서 상태를 갱신합니다.

import os
import tempfile
from typing import Mapping

from fastapi import FastAPI, UploadFile, File, Form, HTTPException, Request, Path
from fastapi.responses import FileResponse, HTMLResponse
from fastapi.templating import Jinja2Templates
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles

from celery.result import AsyncResult
from celery import states

# Celery 앱/태스크는 app.tasks에 있습니다.
# - celery_app: Celery 인스턴스
# - transcribe_task: 실제 자막 생성 작업 함수(워커에서 실행)
from app.tasks import transcribe_task, celery_app
import redis  # /reset_cache에서 Redis 키를 지울 때 사용


def _mark_revoked(task_id: str, reason: str = "User requested cancel"):
    """
    주어진 task_id를 Celery 백엔드에 'REVOKED(취소됨)' 상태로 기록합니다.

    왜 필요한가?
    - 프론트가 /status를 폴링할 때, 바로 '취소됨'을 보이게 하려면
      백엔드 결과 저장소(여기선 Redis)에 상태를 즉시 기록하는 편이 사용자 경험에 좋습니다.
    - mark_as_revoked는 Celery 백엔드 구현에 따라 없을 수 있어 예외 대비(try/except)합니다.
    - 백업 방법으로 store_result를 직접 호출해 REVOKED 상태를 적어둡니다.
    """
    try:
        # Celery 백엔드가 이 API를 지원하면 가장 깔끔하게 취소 상태 저장 가능
        celery_app.backend.mark_as_revoked(task_id, reason=reason)
    except Exception:
        # 일부 백엔드에서는 위 메서드가 없을 수 있음 -> 수동으로 결과 저장
        exc = {"exc_type": "TaskRevokedError", "exc_message": reason}
        celery_app.backend.store_result(task_id, exc, states.REVOKED)


# FastAPI 애플리케이션 인스턴스 생성
app = FastAPI()

# 템플릿 디렉터리 설정 (Jinja2로 index.html 렌더링할 때 사용)
templates = Jinja2Templates(directory="templates")

# /static 경로로 정적 파일(css/js/이미지 등)을 제공
app.mount("/static", StaticFiles(directory="static"), name="static")

# CORS 설정
# - 프론트엔드(동일/다른 도메인)에서 오는 요청을 허용하기 위함
# - 개발 환경에서는 * 로 열어놓고, 운영에서는 필요한 도메인만 허용하는 것이 보안상 안전
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],     # 모든 도메인 허용 (개발 편의)
    allow_credentials=True,
    allow_methods=["*"],     # 모든 HTTP 메서드 허용 (GET/POST/...)
    allow_headers=["*"],     # 모든 헤더 허용
)

# -----------------------------
# 라우트: 메인 페이지
# -----------------------------
@app.get("/", response_class=HTMLResponse)
async def root(request: Request):
    """
    브라우저가 접속했을 때 index.html을 돌려줍니다.
    프론트엔드는 이 페이지에서 파일 업로드/상태 폴링/초기화 등을 합니다.
    """
    return templates.TemplateResponse("index.html", {"request": request})


# -----------------------------
# 라우트: 자막 생성 요청 (파일 업로드)
# -----------------------------
@app.post("/transcribe/")
async def transcribe(
    file: UploadFile = File(...),  # 업로드된 음성/영상 파일
    want_ko: bool = Form(True),    # 한글 자막 생성 여부
    want_en: bool = Form(True),    # 영어(번역) 자막 생성 여부
):
    """
    1) 사용자가 업로드한 파일을 임시 파일로 저장하고
    2) Celery 작업 큐에 '자막 생성' 태스크를 등록합니다.
    3) 등록된 태스크의 ID를 반환합니다. (프론트는 이 ID로 /status를 폴링)
    """
    if not file.filename:
        raise HTTPException(status_code=400, detail="파일이 비어 있습니다.")

    # 파일 확장자 추출 (예: .mp4, .wav)
    #   - 없으면 기본적으로 .wav로 저장
    suffix = "." + file.filename.split(".")[-1] if "." in file.filename else ".wav"
    orig_name = file.filename  # 원래 파일명 (나중에 다운로드 파일명 생성에 사용)

    # 업로드된 파일을 서버의 임시 파일로 저장
    # - 대용량 파일도 안전하게 처리하려고 1MB 단위로 비동기 읽기
    try:
        with tempfile.NamedTemporaryFile(delete=False, suffix=suffix) as tmp:
            while True:
                chunk = await file.read(1024 * 1024)  # 1MB씩 읽기
                if not chunk:
                    break
                tmp.write(chunk)
            tmp_path = tmp.name  # 임시 파일 경로
    except Exception:
        raise HTTPException(status_code=500, detail="파일 저장 실패")

    # Celery 작업 큐에 비동기 태스크 등록 (워커가 실제로 처리)
    try:
        task = transcribe_task.delay(tmp_path, suffix, orig_name, want_ko, want_en)
    except Exception:
        raise HTTPException(status_code=500, detail="작업 요청 실패")

    # 프론트는 이 task_id를 저장해두고 /status/<task_id>를 폴링하여 상태를 확인합니다.
    return {"task_id": task.id}


# -----------------------------
# 라우트: 특정 작업 상태 조회
# -----------------------------
@app.get("/status/{task_id}")
def status(task_id: str):
    """
    Celery 백엔드(여기선 Redis)에 저장된 작업 상태를 조회합니다.
    - PENDING/RECEIVED/STARTED: Celery 기본 상태
    - PROGRESS: 태스크 내부에서 update_state로 올린 커스텀 진행 상태
    - SUCCESS: 완료(다운로드 가능 경로 포함)
    - FAILURE/REVOKED: 실패/취소 (예외 객체가 들어있을 수도 있어 안전하게 처리)
    """
    res = AsyncResult(task_id, app=celery_app)
    state = res.state  # 문자열 상태 값

    # 기본 상태에 대한 "읽기 쉬운 한글" 매핑
    step_map = {"PENDING": 0, "RECEIVED": 1, "STARTED": 2}
    readable = {"PENDING": "대기 중", "RECEIVED": "수신됨", "STARTED": "시작됨"}

    def _detail_from(obj) -> str:
        """
        예외 객체나 dict 형태로 올 수 있는 정보를 '사람이 읽을 수 있는 문자열'로 안전 변환.
        - Celery의 FAILURE 케이스에서 res.info가 Exception일 수 있으므로 방어적으로 처리.
        """
        if isinstance(obj, Mapping):
            for k in ("detail", "message", "exc_message", "error", "status"):
                if k in obj and obj[k]:
                    return str(obj[k])
            return str(obj)  # dict이지만 위 키가 없으면 통째로 문자열화
        try:
            return repr(obj)  # Exception 등은 repr로 보기 좋게 출력
        except Exception:
            return str(obj)

    # Celery 기본 상태 처리
    if state in ("PENDING", "RECEIVED", "STARTED"):
        return {
            "state": state,
            "status": readable.get(state, "진행 중"),
            "step": step_map.get(state, 0),
        }

    # 진행 중(PROGRESS)일 때: 태스크 내부에서 update_state(meta=...)로 제공한 정보가 들어있음
    if state == "PROGRESS":
        info = res.info if isinstance(res.info, Mapping) else {}
        return {
            "state": state,
            "status": info.get("status", "진행 중"),
            "step": info.get("step", 3),
            "srt_path_ko": info.get("srt_path_ko"),
            "srt_path_en": info.get("srt_path_en"),
            "original_filename": info.get("original_filename"),
        }

    # 완료(SUCCESS): 결과 딕셔너리에는 srt 경로 등이 들어있음
    if state == "SUCCESS":
        result = res.result if isinstance(res.result, Mapping) else {}
        return {
            "state": state,
            "status": "완료",
            "step": 6,
            "download_ready": True,
            **result
        }

    # 실패(FAILURE) 또는 취소(REVOKED)
    if state in ("FAILURE", "REVOKED"):
        return {
            "state": state,
            "status": "실패" if state == "FAILURE" else "취소됨",
            "step": -1,
            "detail": _detail_from(res.info),
        }

    # 예측하지 못한 상태 (혹시 모를 확장/커스텀)
    return {"state": state, "status": "알 수 없음", "step": -1}


# -----------------------------
# 라우트: 단일 작업 취소
# -----------------------------
@app.post("/abort/{task_id}")
async def abort_task(task_id: str):
    """
    특정 task를 취소합니다.
    취소 방식:
    1) '협력형 취소' 플래그 설정 (워커가 스텝 사이에서 안전 중단)
    2) 큐에 남아있으면 큐에서 제거(revoke)
    3) 즉시 백엔드 결과를 REVOKED로 기록해서 프론트 폴링에 '취소됨'이 바로 뜨도록 함
    """
    from app.tasks import mark_cancelled

    # 1) 협력형 취소 플래그
    mark_cancelled(task_id)

    # 2) 큐에서 제거 (실행 중인 워커에는 바로 신호가 가지 않을 수 있음)
    celery_app.control.revoke(task_id)

    # 3) 결과 저장소에 '취소됨'으로 바로 기록 (프론트는 즉시 '취소'로 인식)
    _mark_revoked(task_id, reason="User canceled")

    return {"status": "취소됨", "task_id": task_id}


# -----------------------------
# 라우트: 전체 작업 취소(대기/예약/진행 모두)
# -----------------------------
@app.post("/abort_all/")
async def abort_all():
    """
    현재 큐에 있는 모든 작업(대기/예약/진행)을 한 번에 취소합니다.
    절차:
    1) purge()로 '대기 큐' 비우기
    2) inspect로 active/reserved/scheduled 작업들 목록을 수집
       - inspect는 경우에 따라 빈 응답이 올 수 있어 1회 재시도
    3) 각 작업에 대해:
       - 협력형 취소 플래그 설정
       - 가능한 환경에선 terminate=True, signal="SIGKILL"로 강제 종료 시도
         (Windows/solo 풀 등에서는 무시될 수 있음 → 최소 revoke로 큐에서 제거)
       - 백엔드에 REVOKED 상태를 강제로 기록 (프론트 폴링에 즉시 반영)
    응답:
    - 프론트 쪽에서 별도 알람을 띄우지 않게 204 No Content로 반환합니다.
    """
    # 1) '대기 중'인 메시지 제거 (큐 비우기)
    removed = celery_app.control.purge()

    # 2) 현재 실행/예약 중인 작업 목록 수집 (빈 응답 대비 재시도)
    insp = celery_app.control.inspect(timeout=3)
    active    = insp.active()    or {}
    reserved  = insp.reserved()  or {}
    scheduled = insp.scheduled() or {}
    if not (active or reserved or scheduled):
        # 첫 호출이 빈 응답일 수 있으므로 한 번 더 시도
        insp = celery_app.control.inspect(timeout=3)
        active    = active    or (insp.active()    or {})
        reserved  = reserved  or (insp.reserved()  or {})
        scheduled = scheduled or (insp.scheduled() or {})

    from app.tasks import mark_cancelled
    affected = []

    def _mark_revoked_local(task_id: str, reason: str = "Bulk cancel"):
        """백엔드에 REVOKED 상태를 저장(즉시 프론트 반영용, 백업 로직 포함)."""
        try:
            celery_app.backend.mark_as_revoked(task_id, reason=reason)
        except Exception:
            exc = {"exc_type": "TaskRevokedError", "exc_message": reason}
            celery_app.backend.store_result(task_id, exc, states.REVOKED)

    # 3) 각 작업에 대해 취소 처리
    for bucket in (active, reserved, scheduled):
        for _, tasks in (bucket or {}).items():
            for t in tasks:
                # inspect 결과가 dict일 수도 있고 단순 문자열일 수도 있어 안전 처리
                tid = (t.get("id") if isinstance(t, dict) else t)
                if not tid:
                    continue

                # 협력형 취소 플래그 설정 (워커가 스텝 경계에서 중단하도록)
                mark_cancelled(tid)

                # 가능한 환경이면 하드 종료 시도 (무시될 수도 있음)
                try:
                    celery_app.control.revoke(tid, terminate=True, signal="SIGKILL")
                except Exception:
                    # 최소한 큐에서 제거
                    celery_app.control.revoke(tid)

                # 프론트 폴링에 바로 '취소됨'으로 뜨게 결과 저장
                _mark_revoked_local(tid)
                affected.append(tid)

    # 4) 우리는 프론트에 굳이 JSON 내용을 주지 않음 (알람도 원치 않기 때문)
    #    204 No Content를 돌려주면 브라우저 fetch는 성공 처리되지만 바디는 없음.
    return HTMLResponse(status_code=204)


# -----------------------------
# 라우트: SRT 다운로드
# -----------------------------
@app.get("/download/{task_id}/{lang}")
def download(task_id: str, lang: str = Path(...)):
    """
    완료된 작업의 SRT 파일을 다운로드합니다.
    - 반드시 작업 상태가 SUCCESS여야 합니다. (아니면 404)
    - lang은 "ko" 또는 "en"과 같이 key로 사용됩니다.
    """
    res = AsyncResult(task_id, app=celery_app)

    # 아직 성공적으로 끝나지 않았다면 다운로드 불가
    if res.state != "SUCCESS":
        raise HTTPException(status_code=404, detail="자막 준비되지 않음")

    # 성공한 경우 result는 dict 형태로 srt 경로와 원본 파일명이 들어 있음
    data = res.result if isinstance(res.result, Mapping) else {}
    srt = data.get(f"srt_path_{lang}")   # 요청한 언어의 SRT 경로
    orig = data.get("original_filename") # 원본 파일명(다운로드 파일명 구성용)

    if not srt or not os.path.exists(srt):
        raise HTTPException(status_code=404, detail="SRT 파일 없음")

    # 다운로드 파일명 예: myvideo_ko.srt
    fname = f"{os.path.splitext(orig)[0]}_{lang}.srt"
    return FileResponse(srt, media_type="application/x-subrip", filename=fname)


# -----------------------------
# 라우트: Redis 캐시(결과/취소세트) 완전 초기화
# -----------------------------
@app.post("/reset_cache/")
def reset_cache():
    """
    Redis에 남아있는 Celery 결과 메타와 '취소된 작업 목록' 세트를 모두 삭제합니다.
    보통 아래 순서로 사용합니다:
      1) /abort_all  (전체 취소 요청)
      2) /reset_cache (결과/플래그 키 정리)
      3) 서버/워커 재시작 (필요시)
    """
    r = redis.Redis.from_url("redis://localhost:6379/0")
    deleted = 0

    # Celery 결과 메타 키 삭제 (형식: celery-task-meta-*)
    for key in r.scan_iter(match="celery-task-meta-*"):
        r.delete(key)
        deleted += 1

    # 우리가 협력형 취소에 사용한 취소 플래그 세트 제거
    r.delete("cancelled_tasks")

    return {"status": "OK", "deleted_meta": deleted, "cancel_set_cleared": True}
