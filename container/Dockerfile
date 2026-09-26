# ─── Build stage ──────────────────────────────────────────────────────────────
# Use the official FFmpeg image as the base for the video tooling layer.
FROM jrottenberg/ffmpeg:7.1-alpine AS ffmpeg

# ─── Runtime stage ────────────────────────────────────────────────────────────
# Start from a slim Python image and copy FFmpeg in from the build stage.
FROM python:3.12-slim

# Bring in the ffmpeg binary and its libraries.
COPY --from=ffmpeg /usr/local/bin/ffmpeg /usr/local/bin/ffmpeg
COPY --from=ffmpeg /usr/local/bin/ffprobe /usr/local/bin/ffprobe
COPY --from=ffmpeg /usr/local/lib /usr/local/lib
COPY --from=ffmpeg /usr/lib /usr/lib

# Fonts for burned-in captions (Inter is what the pipeline's ASS file targets).
RUN apt-get update && \
    apt-get install -y --no-install-recommends \
      fonts-inter \
      ca-certificates \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# Install Python dependencies first (layer caching).
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy the pipeline code and the HTTP server wrapper.
COPY pipeline/ ./pipeline/
COPY server.py .

# The container listens on 8080. Cloudflare routes to this port.
EXPOSE 8080

# Run the HTTP server. The server blocks on port 8080 and handles
# POST /generate requests from the Worker.
CMD ["python", "-u", "server.py"]
