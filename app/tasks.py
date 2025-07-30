import os
import torch
import whisper
import tempfile
import threading
import time
from celery import Celery
from whisper import load_model
from app.utils import write_srt

#Celery 설정
# Redis를 브로커와 백엔드로 사용하여 작업 큐를 관리
celery_app = Celery(
    "worker",
    broker="redis://localhost:6379/0",  #작업 큐 전달용
    backend="redis://localhost:6379/0", #작업 상태 및 결과저장용
)

# Whisper 모델 로드
# GPU가 사용 가능하면 CUDA를 사용하고, 그렇지 않으면 CPU를 사용 
device = "cuda" if torch.cuda.is_available() else "cpu"
model = whisper.load_model("medium", device=device)

#임시 파일 자동 삭제 
def delayed_delete(path, delay=86400):  # 86400초 = 24시간
    def _delete():
        time.sleep(delay)
        if os.path.exists(path):
            os.remove(path)
    threading.Thread(target=_delete, daemon=True).start()

# Celery 작업 정의
# 비동기 작업 큐에 등록된 작업을 처리하는 함수
@celery_app.task(bind=True)
def transcribe_task(self, file_bytes, suffix, original_filename, want_ko=True, want_en=True):


    tmp_path = None
    ko_temp = en_temp = None
    # 1) 임시 파일 생성
    self.update_state(state="PROGRESS", meta={"step": 1, "status": "임시 파일 생성 중"})

    try:
        with tempfile.NamedTemporaryFile(delete=False, suffix=suffix) as tmp:
            tmp.write(file_bytes)
            tmp_path = tmp.name

        result = {}
        # 2) 음성 인식 - 한국자막생성
        if want_ko:
            self.update_state(state="PROGRESS", meta={"step": 2, "status": "한국어 자막 생성 중"})
            result_ko = model.transcribe(tmp_path, task="transcribe", language="ko")
            ko_temp = tempfile.NamedTemporaryFile(delete=False, suffix=".srt")
            write_srt(result_ko["segments"], ko_temp.name)
            result["srt_path_ko"] = ko_temp.name
        # 3) 음성 인식 - 영어자막생성
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

        if tmp_path and os.path.exists(tmp_path):
            os.remove(tmp_path)
        if ko_temp:
            delayed_delete(ko_temp.name)
        if en_temp:
            delayed_delete(en_temp.name)
