FROM python:3.12-slim

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

# State file lives in a volume so it survives container restarts
VOLUME ["/app/data"]
ENV KERNEL_BOT_STATE_DIR=/app/data
ENV PYTHONIOENCODING=utf-8

CMD ["python", "-m", "kernel_lore_bot"]
