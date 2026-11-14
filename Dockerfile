# AI Video Maker - Docker Image
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

# Install Python dependencies
RUN pip install --no-cache-dir -r requirements.txt

# Download spacy model required by Kokoro TTS (via misaki/en.py)
RUN python -m spacy download en_core_web_sm

# Copy application code
COPY . .

# Create necessary directories and non-root user
RUN mkdir -p output temp cache projects \
    && useradd -m -r appuser \
    && chown -R appuser:appuser /app
USER appuser

# Expose API port
EXPOSE 8000

# Default command: run API server
CMD ["python", "run_api.py"]
