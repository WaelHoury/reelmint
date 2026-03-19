"""RQ Worker for social publishing jobs (separate from video generation).

Usage:
    python run_publish_worker.py

This worker processes video publish jobs queued by the API.
It runs on a dedicated 'video_publish' queue so uploads to YouTube/TikTok/Instagram
never block the video generation workers.
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
AI Video Maker - Publish Worker
{'='*60}

Connecting to Redis: {settings.redis_url}
Listening on queue: video_publish
(Separate from video_generation workers)

Waiting for jobs...
{'='*60}
""")

    redis_conn = Redis.from_url(settings.redis_url)
    publish_queue = Queue('video_publish', connection=redis_conn)
    worker = Worker([publish_queue], connection=redis_conn)
    worker.work()


if __name__ == "__main__":
    main()
