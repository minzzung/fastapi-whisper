import os
import tempfile
import threading
import time
from celery import Celery
from whisper import load_model
from app.utils import write_srt

celery_app = Celery(
    "worker",
    broker="redis://localhost:6379/0",
    backend="redis://localhost:6379/0",
)

model = load_model("medium")
is_processing = False  # 전역 상태 (병렬 방지)

def delayed_delete(path, delay=300):
    def _delete():
        time.sleep(delay)
        if os.path.exists(path):
            os.remove(path)
    threading.Thread(target=_delete, daemon=True).start()

@celery_app.task(bind=True)
def transcribe_task(self, file_bytes, suffix, original_filename, want_ko=True, want_en=True):
    global is_processing
    while is_processing:
        time.sleep(1)  # 작업 중이면 대기
    is_processing = True

    tmp_path = None
    ko_temp = en_temp = None
    self.update_state(state="PROGRESS", meta={"step": 1, "status": "임시 파일 생성 중"})

    try:
        with tempfile.NamedTemporaryFile(delete=False, suffix=suffix) as tmp:
            tmp.write(file_bytes)
            tmp_path = tmp.name

        result = {}

        if want_ko:
            self.update_state(state="PROGRESS", meta={"step": 2, "status": "한국어 자막 생성 중"})
            result_ko = model.transcribe(tmp_path, task="transcribe", language="ko")
            ko_temp = tempfile.NamedTemporaryFile(delete=False, suffix=".srt")
            write_srt(result_ko["segments"], ko_temp.name)
            result["srt_path_ko"] = ko_temp.name

        if want_en:
            self.update_state(state="PROGRESS", meta={"step": 3, "status": "영어 자막 생성 중"})
            result_en = model.transcribe(tmp_path, task="translate", language="ko")
            en_temp = tempfile.NamedTemporaryFile(delete=False, suffix=".srt")
            write_srt(result_en["segments"], en_temp.name)
            result["srt_path_en"] = en_temp.name

        result["original_filename"] = original_filename
        return result

    except Exception as e:
        self.update_state(state="FAILURE", meta={"step": -1, "detail": str(e)})
        raise
    finally:
        is_processing = False
        if tmp_path and os.path.exists(tmp_path):
            os.remove(tmp_path)
        if ko_temp:
            delayed_delete(ko_temp.name)
        if en_temp:
            delayed_delete(en_temp.name)
