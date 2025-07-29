import os
import tempfile
import threading
import time
from celery import Celery
from whisper import load_model
from app.utils import write_srt

# Celery 설정
celery_app = Celery(
    "worker",
    broker="redis://localhost:6379/0",     # Redis 브로커 사용
    backend="redis://localhost:6379/0",    # Redis 결과 백엔드 사용
)

# Whisper 모델 로드 (tiny, base, small, medium, large 중 선택)
model = load_model("medium")  # 실사용은 medium 이상 추천

# 임시 파일 자동 삭제 함수
def delayed_delete(path, delay=300):
    def _delete():
        time.sleep(delay)
        if os.path.exists(path):
            os.remove(path)
    threading.Thread(target=_delete, daemon=True).start()

# 비동기 자막 생성 작업
@celery_app.task(bind=True)
def transcribe_task(self, file_bytes, suffix, original_filename, want_ko=True, want_en=True):
    # 작업 상태 업데이트
    self.update_state(state="PROGRESS", meta={"status": "처리 시작"})

    # Whisper가 처리할 수 있도록 임시 파일로 저장
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
        # 에러 발생 시 Celery에 예외 정보 문자열로 전달 (직렬화 오류 방지)
        self.update_state(state="FAILURE", meta={"status": "실패", "detail": str(e)})
        return {"status": "실패", "detail": str(e)}

    finally:
        # 원본 임시 파일 삭제
        os.remove(tmp_path)

    # 5분 후 자막 파일 삭제 (원하면 주석 처리 가능)
    # if ko_temp: delayed_delete(ko_temp.name)
    # if en_temp: delayed_delete(en_temp.name)

    return {
        "original_filename": original_filename,
        "srt_path_ko": ko_temp.name if ko_temp else None,
        "srt_path_en": en_temp.name if en_temp else None,
        "status": "완료"
    }
