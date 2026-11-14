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
Reelmint - RQ Worker
{'='*60}

Connecting to Redis: {settings.redis_url}
Listening on queue: video_generation
(Clip search runs on the API server)

Waiting for jobs...
{'='*60}
""")

    redis_conn = Redis.from_url(settings.redis_url)
    video_queue = Queue('video_generation', connection=redis_conn)
    worker = Worker([video_queue], connection=redis_conn)
    # with_scheduler=True is required for hands-off batches: the batch runner
    # re-queues itself with queue.enqueue_in() to wake up for retries, which
    # only runs if an RQ scheduler is active for this queue.
    worker.work(with_scheduler=True)


if __name__ == "__main__":
    main()
