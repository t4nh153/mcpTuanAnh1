FROM python:3.11-slim

# Cài đặt FFmpeg để nén mọi luồng nhạc sang MP3 chuẩn cho mạch ESP32
RUN apt-get update && \
    apt-get install -y --no-install-recommends ffmpeg && \
    rm -rf /var/lib/apt/lists/*

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

CMD ["python", "mcp_pipe.py", "main_tools.py"]
