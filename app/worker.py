"""
worker.py
비동기 작업 큐 라이브러리(worker가 큐에 있는 작업 하나씩 실행)
"""

from app.tasks import celery_app

if __name__ == "__main__":
    # argv에 worker 명령어 포함
    celery_app.start(argv=['worker', '-l', 'info'])