FROM python:3.11-slim

# FFmpeg is used to extract audio and render the selected clips.
RUN apt-get update && apt-get install -y --no-install-recommends ffmpeg ca-certificates curl && rm -rf /var/lib/apt/lists/*

# yt-dlp now needs an external JavaScript runtime for full YouTube support.
# Copy the official Deno binary into the Python image.
COPY --from=denoland/deno:bin-2.9.4 /deno /usr/local/bin/deno

WORKDIR /app
COPY backend/requirements.txt /app/backend/requirements.txt
RUN pip install --no-cache-dir -U "yt-dlp[default]" && pip install --no-cache-dir -r /app/backend/requirements.txt

ENV PATH="/usr/local/bin:${PATH}"
ENV YTDLP_JS_RUNTIME="deno"

COPY . /app
EXPOSE 8000
CMD ["uvicorn","backend.main:app","--host","0.0.0.0","--port","8000"]
