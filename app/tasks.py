import os
import tempfile
import threading
import time
from celery import Celery, states
from whisper import load_model
from app.utils import write_srt

# Celery 설정
celery_app = Celery(
    "worker",
    broker="redis://localhost:6379/0",
    backend="redis://localhost:6379/0",
)

# Whisper 모델 로드
model = load_model("medium")

# 일정 시간 후 파일 삭제 함수
def delayed_delete(path, delay=3600*12):
    def _delete():
        time.sleep(delay)
        if os.path.exists(path):
            os.remove(path)
    threading.Thread(target=_delete, daemon=True).start()

# 자막 생성 Celery 작업
@celery_app.task(bind=True)
def transcribe_task(self, file_path, suffix, original_filename, want_ko=True, want_en=True):
    try:
        task_id = self.request.id
        self.update_state(state="PROGRESS", meta={"status": "사용자 요청 수신 완료"})

        # 파일 읽고 임시 경로에 저장
        with open(file_path, "rb") as f:
            file_bytes = f.read()

        with tempfile.NamedTemporaryFile(delete=False, suffix=suffix) as tmp:
            tmp.write(file_bytes)
            tmp_path = tmp.name

        os.remove(file_path)

        self.update_state(state="PROGRESS", meta={"status": "Whisper 모델 처리 중"})

        print("[DEBUG] Whisper 모델 시작")
        ko_result = model.transcribe(tmp_path, language="ko")
        print("[DEBUG] Whisper 모델 완료")

        ko_srt_path = os.path.join(tempfile.gettempdir(), f"{task_id}_ko.srt")
        write_srt(ko_result["segments"], ko_srt_path)

        en_srt_path = None
        if want_en:
            self.update_state(state="PROGRESS", meta={"status": "영어 번역 중"})
            en_result = model.transcribe(tmp_path, task="translate")
            en_srt_path = os.path.join(tempfile.gettempdir(), f"{task_id}_en.srt")
            write_srt(en_result["segments"], en_srt_path)

        delayed_delete(tmp_path)
        delayed_delete(ko_srt_path)
        if en_srt_path:
            delayed_delete(en_srt_path)

        return {
            "status": "완료",
            "srt_path_ko": ko_srt_path if want_ko else None,
            "srt_path_en": en_srt_path if want_en else None,
            "original_filename": original_filename
        }

    except Exception as e:
        self.update_state(
            state=states.FAILURE,
            meta={
                "status": "실패",
                "detail": str(e),
                "exc_type": type(e).__name__,
            }
        )
        raise
