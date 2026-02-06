"""RQ Worker entry point for video generation jobs.

Usage:
    python run_worker.py

This worker processes video generation jobs queued by the API.
Make sure Redis is running before starting the worker.
"""

import sys
from pathlib import Path
from dotenv import load_dotenv

# Load environment variables
load_dotenv()

# Ensure app is importable
sys.path.insert(0, str(Path(__file__).parent))

from redis import Redis
from rq import Worker, Queue

from app.core.config import settings


def main():
    print(f"""
{'='*60}
AI Video Maker - RQ Worker
{'='*60}

Connecting to Redis: {settings.redis_url}
Listening on queue: video_generation

Waiting for jobs...
{'='*60}
""")

    redis_conn = Redis.from_url(settings.redis_url)
    queue = Queue('video_generation', connection=redis_conn)
    worker = Worker([queue], connection=redis_conn)
    worker.work()


if __name__ == "__main__":
    main()
