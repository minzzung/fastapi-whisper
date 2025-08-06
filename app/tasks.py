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

# Celery 애플리케이션 설정
# Redis를 브로커와 결과 저장소로 사용
celery_app = Celery(
    "worker",
    broker="redis://localhost:6379/0",   # 작업 큐 브로커
    backend="redis://localhost:6379/0",  # 작업 상태/결과 저장소
)

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
@celery_app.task(bind=True, soft_time_limit=3600)  # 최대 1시간 제한
def transcribe_task(self, file_path: str, suffix: str, original_filename: str, want_ko=True, want_en=True):
    task_id = self.request.id
    print(f"[DEBUG] Task 시작: {task_id}")

    # 1단계: 상태 업데이트 - 파일 처리 시작
    self.update_state(state="PROGRESS", meta={"status": "파일 준비 중", "step": 1})

    # 파일을 읽고 다시 임시 파일로 복사 (Whisper가 사용할 준비 완료)
    print(f"[DEBUG] 파일 읽기 및 임시 저장: {file_path}")
    with open(file_path, "rb") as src, tempfile.NamedTemporaryFile(delete=False, suffix=suffix) as tmp:
        tmp.write(src.read())
        tmp_path = tmp.name
    os.remove(file_path)  # 업로드 파일은 삭제하고 tmp 파일만 사용

    # soft revoke(사용자 취소 요청) 여부 확인
    if getattr(self.request, "is_revoked", lambda: False)():
        print(f"[ABORT] Task 취소됨 - 파일 준비 이후")
        self.update_state(state=states.REVOKED, meta={"status": "사용자 취소", "step": -1})
        raise Ignore()  # 작업 중단

    # 2단계: 한글 자막 생성
    self.update_state(state="PROGRESS", meta={"status": "한글 자막 생성 중", "step": 2})
    print("[DEBUG] Whisper 모델로 한글 자막 생성 시작")

    if getattr(self.request, "is_revoked", lambda: False)():
        print(f"[ABORT] Task 취소됨 - 한글 처리 전")
        self.update_state(state=states.REVOKED, meta={"status": "사용자 취소", "step": -1})
        raise Ignore()

    # Whisper를 통해 한국어 자막 생성
    ko = model.transcribe(tmp_path, language="ko")
    ko_srt = os.path.join(tempfile.gettempdir(), f"{task_id}_ko.srt")
    write_srt(ko["segments"], ko_srt)
    print(f"[DEBUG] 한글 SRT 저장 완료: {ko_srt}")

    # 영어 자막이 요청된 경우
    en_srt = None
    if want_en:
        self.update_state(state="PROGRESS", meta={"status": "영어 자막 생성 중", "step": 3})
        print("[DEBUG] Whisper 모델로 영어 자막 생성 시작")

        if getattr(self.request, "is_revoked", lambda: False)():
            print(f"[ABORT] Task 취소됨 - 영어 처리 전")
            self.update_state(state=states.REVOKED, meta={"status": "사용자 취소", "step": -1})
            raise Ignore()

        # Whisper를 통해 영어 자막 생성 (번역 기반)
        en = model.transcribe(tmp_path, task="translate")
        en_srt = os.path.join(tempfile.gettempdir(), f"{task_id}_en.srt")
        write_srt(en["segments"], en_srt)
        print(f"[DEBUG] 영어 SRT 저장 완료: {en_srt}")

    # 4단계: 후처리 단계 - 파일 자동 삭제 예약
    self.update_state(state="PROGRESS", meta={"status": "후처리 및 클린업", "step": 4})
    print("[DEBUG] 후처리 단계: 자동 삭제 스케줄링 시작")
    for p in [tmp_path, ko_srt, en_srt]:
        if p:
            delayed_delete(p)  # 12시간 후 삭제 예약
            print(f"[DEBUG] 자동 삭제 예약: {p}")

    print(f"[DEBUG] Task 완료: {task_id}")

    # 최종 결과 반환 (FastAPI에서 상태 확인 시 사용됨)
    return {
        "srt_path_ko": ko_srt,
        "srt_path_en": en_srt,
        "original_filename": original_filename,
    }
