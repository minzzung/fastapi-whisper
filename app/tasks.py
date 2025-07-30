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
    try:
        self.update_state(state="PROGRESS", meta={"step": 1, "status": "📩 사용자 요청 수신 완료"})
        if not file_bytes:
            raise ValueError("파일이 비어 있습니다.")
        
        self.update_state(state="PROGRESS", meta={"step": 2, "status": "📁 파일 업로드 확인 중"})
        self.update_state(state="PROGRESS", meta={"step": 3, "status": "⚙️ Whisper 모델 준비 중"})

        with tempfile.NamedTemporaryFile(delete=False, suffix=suffix) as tmp:
            self.update_state(state="PROGRESS", meta={"step": 4, "status": "💾 임시 파일 생성 중"})
            tmp.write(file_bytes)
            tmp_path = tmp.name

        ko_temp = en_temp = None

        if want_ko:
            self.update_state(state="PROGRESS", meta={"step": 5, "status": "📝 한국어 자막 생성 시작"})
            result_ko = model.transcribe(tmp_path, task="transcribe")
            self.update_state(state="PROGRESS", meta={"step": 6, "status": "✅ 한국어 자막 생성 완료"})
            ko_temp = tempfile.NamedTemporaryFile(delete=False, suffix="_ko.srt")
            write_srt(result_ko["segments"], ko_temp.name)

        if want_en:
            self.update_state(state="PROGRESS", meta={"step": 7, "status": "🌐 영어 자막 번역 시작"})
            result_en = model.transcribe(tmp_path, task="translate")
            self.update_state(state="PROGRESS", meta={"step": 8, "status": "📜 SRT 포맷 변환 중 (영문)"})
            en_temp = tempfile.NamedTemporaryFile(delete=False, suffix="_en.srt")
            write_srt(result_en["segments"], en_temp.name)

        self.update_state(state="PROGRESS", meta={
            "step": 9,
            "status": "🎉 전체 처리 완료",
            "srt_path_ko": ko_temp.name if ko_temp else None,
            "srt_path_en": en_temp.name if en_temp else None,
            "original_filename": original_filename
        })

    except Exception as e:
        self.update_state(state="FAILURE", meta={"step": -1, "status": "❌ 실패", "detail": str(e)})
        return {"status": "실패", "detail": str(e)}
    finally:
        if os.path.exists(tmp_path):
            os.remove(tmp_path)

    return {
        "original_filename": original_filename,
        "srt_path_ko": ko_temp.name if ko_temp else None,
        "srt_path_en": en_temp.name if en_temp else None,
        "status": "완료"
    }
