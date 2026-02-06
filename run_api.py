"""API server entry point.

Usage:
    python run_api.py

Starts the FastAPI server on http://localhost:8000

API Documentation:
    - Swagger UI: http://localhost:8000/docs
    - ReDoc: http://localhost:8000/redoc
"""

import uvicorn
from dotenv import load_dotenv

# Load environment variables
load_dotenv()

from app.core.config import settings

if __name__ == "__main__":
    print(f"""
{'='*60}
AI Video Maker API Server
{'='*60}

Starting server at http://{settings.api_host}:{settings.api_port}

API Documentation:
  - Swagger UI: http://localhost:{settings.api_port}/docs
  - ReDoc: http://localhost:{settings.api_port}/redoc

Make sure Redis is running:
  - Session store: {settings.redis_url}

To start RQ worker (in another terminal):
  rq worker video_generation --url {settings.redis_url}

{'='*60}
""")

    uvicorn.run(
        "app.api.main:app",
        host=settings.api_host,
        port=settings.api_port,
        reload=True,  # Enable auto-reload for development
    )
