# =====================
# tasks.py
# =====================
import os
import tempfile
import threading
import time
from celery import Celery, states
from celery.exceptions import Ignore
from whisper import load_model
from app.utils import write_srt

celery_app = Celery(
    "worker",
    broker="redis://localhost:6379/0",
    backend="redis://localhost:6379/0",
)

# 모델 로드 (워커 프로세스 당 1회)
model = load_model("medium")

# 파일 자동 삭제 함수
def delayed_delete(path: str, delay: int = 3600 * 12):
    def _del():
        time.sleep(delay)
        if os.path.exists(path):
            os.remove(path)
            print(f"[DEBUG] 자동 삭제된 파일: {path}")
    threading.Thread(target=_del, daemon=True).start()

@celery_app.task(bind=True, soft_time_limit=3600)
def transcribe_task(self, file_path: str, suffix: str, original_filename: str, want_ko=True, want_en=True):
    task_id = self.request.id
    print(f"[DEBUG] Task 시작: {task_id}")

    # 1) 파일 준비 단계
    self.update_state(state="PROGRESS", meta={"status": "파일 준비 중", "step": 1})
    print(f"[DEBUG] 파일 읽기 및 임시 저장: {file_path}")
    with open(file_path, "rb") as src, tempfile.NamedTemporaryFile(delete=False, suffix=suffix) as tmp:
        tmp.write(src.read())
        tmp_path = tmp.name
    os.remove(file_path)

    # soft revoke 체크 (파일 준비 후)
    if getattr(self.request, "is_revoked", lambda: False)():
        print(f"[DEBUG] Task 소프트 취소 감지: {task_id}")
        self.update_state(state=states.REVOKED, meta={"status": "사용자 취소", "step": -1})
        raise Ignore()

    # 2) 한글 자막 생성
    self.update_state(state="PROGRESS", meta={"status": "한글 자막 생성 중", "step": 2})
    print("[DEBUG] Whisper 모델로 한글 자막 생성 시작")
    ko = model.transcribe(tmp_path, language="ko")
    ko_srt = os.path.join(tempfile.gettempdir(), f"{task_id}_ko.srt")
    write_srt(ko["segments"], ko_srt)
    print(f"[DEBUG] 한글 SRT 저장 완료: {ko_srt}")

    # 3) 영어 자막 생성 (선택)
    en_srt = None
    if want_en:
        self.update_state(state="PROGRESS", meta={"status": "영어 자막 생성 중", "step": 3})
        print("[DEBUG] Whisper 모델로 영어 자막 생성 시작")
        en = model.transcribe(tmp_path, task="translate")
        en_srt = os.path.join(tempfile.gettempdir(), f"{task_id}_en.srt")
        write_srt(en["segments"], en_srt)
        print(f"[DEBUG] 영어 SRT 저장 완료: {en_srt}")

    # 4) 최종 처리 및 클린업
    self.update_state(state="PROGRESS", meta={"status": "후처리 및 클린업", "step": 4})
    print("[DEBUG] 후처리 단계: 자동 삭제 스케줄링 시작")
    for p in [tmp_path, ko_srt, en_srt]:
        if p:
            delayed_delete(p)
            print(f"[DEBUG] 자동 삭제 예약: {p}")

    # 5) 완료
    print(f"[DEBUG] Task 완료: {task_id}")
    return {"srt_path_ko": ko_srt, "srt_path_en": en_srt, "original_filename": original_filename}
