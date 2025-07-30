"""
worker.py
Celery 워커 실행 파일
"""

from app.tasks import celery_app

if __name__ == "__main__":
    # argv에 worker 명령어 포함
    celery_app.start(argv=['worker', '-l', 'info'])