# tasks.py

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

model = load_model("tiny")

def delayed_delete(path, delay=300):
    def _delete():
        time.sleep(delay)
        if os.path.exists(path):
            os.remove(path)
    threading.Thread(target=_delete, daemon=True).start()

@celery_app.task(bind=True)
def transcribe_task(self, file_bytes, suffix, original_filename, want_ko=True, want_en=True):
    self.update_state(state="PROGRESS", meta={"status": "처리 시작"})

    with tempfile.NamedTemporaryFile(delete=False, suffix=suffix) as tmp:
        tmp.write(file_bytes)
        tmp_path = tmp.name

    ko_temp = en_temp = None

    try:
        if want_ko:
            self.update_state(state="PROGRESS", meta={"status": "한국어 자막 생성 중"})
            result_ko = model.transcribe(tmp_path, task="transcribe")
            ko_temp = tempfile.NamedTemporaryFile(delete=False, suffix="_ko.srt")
            write_srt(result_ko["segments"], ko_temp.name)

        if want_en:
            self.update_state(state="PROGRESS", meta={"status": "영어 자막 생성 중"})
            result_en = model.transcribe(tmp_path, task="translate")
            en_temp = tempfile.NamedTemporaryFile(delete=False, suffix="_en.srt")
            write_srt(result_en["segments"], en_temp.name)

    except Exception as e:
        self.update_state(state="FAILURE", meta={"status": "실패", "detail": str(e)})
        raise e
    finally:
        os.remove(tmp_path)

    if ko_temp: delayed_delete(ko_temp.name)
    if en_temp: delayed_delete(en_temp.name)

    return {
        "original_filename": original_filename,
        "srt_path_ko": ko_temp.name if ko_temp else None,
        "srt_path_en": en_temp.name if en_temp else None,
        "status": "완료"
    }