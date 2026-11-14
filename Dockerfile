# Reelmint - Docker Image
# Supports both API server and RQ worker

FROM python:3.11-slim

# Install system dependencies
# fonts-montserrat: the caption presets use Montserrat Bold
RUN apt-get update && apt-get install -y --no-install-recommends \
    ffmpeg \
    fonts-montserrat \
    fontconfig \
    && fc-cache -f \
    && rm -rf /var/lib/apt/lists/*

# Set working directory
WORKDIR /app

# Copy requirements first (for better caching)
COPY requirements.txt .

# CPU-only PyTorch: the default wheels bundle several GB of CUDA libraries.
# NVIDIA GPU users: change the index URL to a CUDA build
# (e.g. https://download.pytorch.org/whl/cu124) and give the containers GPU access.
RUN pip install --no-cache-dir torch torchvision --index-url https://download.pytorch.org/whl/cpu

# Install Python dependencies (torch is already satisfied by the line above)
RUN pip install --no-cache-dir -r requirements.txt

# Download spacy model required by Kokoro TTS (via misaki/en.py)
RUN python -m spacy download en_core_web_sm

# Copy application code
COPY . .

# Non-root user with uid/gid 1000, the usual uid of the first user on a Linux
# host, so bind-mounted folders (output, projects, cache, models) stay writable.
RUN mkdir -p output temp cache projects models \
    && groupadd -g 1000 appuser \
    && useradd -m -u 1000 -g appuser appuser \
    && chown -R appuser:appuser /app
USER appuser

# Model downloads (Kokoro, Whisper, OpenCLIP) go to the mounted ./cache, so they
# happen once and are shared by the API and every worker.
ENV HF_HOME=/app/cache/huggingface \
    XDG_CACHE_HOME=/app/cache

# Expose API port
EXPOSE 8000

# Default command: run API server
CMD ["python", "run_api.py"]
