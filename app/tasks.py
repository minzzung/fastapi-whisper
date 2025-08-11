# =====================
# tasks.py - 자막 생성 작업(Celery Worker용)
# =====================

import os
import tempfile
import threading
import time
from celery import Celery, states
from celery.exceptions import Ignore
from whisper import load_model
from app.utils import write_srt  # SRT 파일로 저장하는 유틸 함수
from celery.backends.redis import RedisBackend

# ====== Celery 앱 생성 후 설정 보강 ======
celery_app = Celery(
    "worker",
    broker="redis://localhost:6379/0",   # 작업 큐 브로커
    backend="redis://localhost:6379/0",  # 작업 상태/결과 저장소
)

celery_app.conf.update(
    task_track_started=True,                 # STARTED 표시 활성화
    worker_prefetch_multiplier=1,            # 한 번에 하나씩 가져오기(공정/예측 가능)
    task_acks_late=True,                     # 실패/죽음 시 재전송
    broker_transport_options={'visibility_timeout': 3600},  # 워커 죽으면 1시간 후 재큐잉
    result_expires=21600,                    # 결과 6시간 보관(원하면 더 짧게)
    result_persistent=False                  # 재시작 꼬임 줄이려면 False 권장(필요 시 True)
)

# ====== 취소 플래그 유틸 ======
from celery.backends.redis import RedisBackend

def _cancel_key():
    return "cancelled_tasks"

def mark_cancelled(task_id: str):
    backend: RedisBackend = celery_app.backend  # type: ignore
    backend.client.sadd(_cancel_key(), task_id)

def is_cancelled(task_id: str) -> bool:
    backend: RedisBackend = celery_app.backend  # type: ignore
    return backend.client.sismember(_cancel_key(), task_id)

# Whisper 음성 인식 모델 사전 로딩
# 워커 프로세스가 시작될 때 1회만 로딩 (성능 최적화)
model = load_model("medium")


# 일정 시간 후 지정 파일을 자동 삭제하는 백그라운드 스레드 함수
def delayed_delete(path: str, delay: int = 3600 * 12):  # 기본 12시간 후 삭제
    def _del():
        time.sleep(delay)  # 지정된 시간 동안 대기
        if os.path.exists(path):
            os.remove(path)  # 파일 존재 시 삭제
            print(f"[DEBUG] 자동 삭제된 파일: {path}")
    threading.Thread(target=_del, daemon=True).start()


# 자막 생성 작업 함수 (Celery 태스크)
@celery_app.task(bind=True, soft_time_limit=3600)
def transcribe_task(self, file_path: str, suffix: str, original_filename: str, want_ko=True, want_en=True):
    task_id = self.request.id
    print(f"[DEBUG] Task 시작: {task_id}")

    def _check_cancel(stage: str):
        if is_cancelled(task_id):
            print(f"[ABORT] Task 취소됨 - {stage}")
            self.update_state(state=states.REVOKED, meta={"status": f"사용자 취소({stage})", "step": -1})
            raise Ignore()

    # 1) 파일 준비
    self.update_state(state="PROGRESS", meta={"status": "파일 준비 중", "step": 1})
    _check_cancel("파일 준비 전")

    print(f"[DEBUG] 파일 읽기 및 임시 저장: {file_path}")
    with open(file_path, "rb") as src, tempfile.NamedTemporaryFile(delete=False, suffix=suffix) as tmp:
        tmp.write(src.read())
        tmp_path = tmp.name
    os.remove(file_path)
    _check_cancel("파일 준비 후")

    # 2) KO 자막
    ko_srt = None
    if want_ko:
        self.update_state(state="PROGRESS", meta={"status": "한글 자막 생성 중", "step": 2})
        print("[DEBUG] Whisper 모델로 한글 자막 생성 시작")
        _check_cancel("KO 시작 전")

        ko = model.transcribe(tmp_path, language="ko")
        ko_srt = os.path.join(tempfile.gettempdir(), f"{task_id}_ko.srt")
        write_srt(ko["segments"], ko_srt)
        print(f"[DEBUG] 한글 SRT 저장 완료: {ko_srt}")
        _check_cancel("KO 완료 후")

    # 3) EN 자막
    en_srt = None
    if want_en:
        self.update_state(state="PROGRESS", meta={"status": "영어 자막 생성 중", "step": 3})
        print("[DEBUG] Whisper 모델로 영어 자막 생성 시작")
        _check_cancel("EN 시작 전")

        en = model.transcribe(tmp_path, task="translate")
        en_srt = os.path.join(tempfile.gettempdir(), f"{task_id}_en.srt")
        write_srt(en["segments"], en_srt)
        print(f"[DEBUG] 영어 SRT 저장 완료: {en_srt}")
        _check_cancel("EN 완료 후")

    # 4) 후처리/자동 삭제 예약
    self.update_state(state="PROGRESS", meta={"status": "후처리 및 클린업", "step": 4})
    for p in [tmp_path, ko_srt, en_srt]:
        if p:
            delayed_delete(p)
            print(f"[DEBUG] 자동 삭제 예약: {p}")

    print(f"[DEBUG] Task 완료: {task_id}")
    return {
        "srt_path_ko": ko_srt,
        "srt_path_en": en_srt,
        "original_filename": original_filename,
    }
