FROM python:3.12-slim

# 中文字体：PowerPoint/PDF 预览需要，同时避免 uharfbuzz 报缺字形
RUN apt-get update && apt-get install -y --no-install-recommends \
      fonts-noto-cjk \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

ENV PYTHONUNBUFFERED=1 \
    PYTHONIOENCODING=utf-8

# Hugging Face Spaces 默认 7860；Render 会注入 $PORT 覆盖
EXPOSE 7860

CMD ["sh", "-c", "uvicorn app:app --host 0.0.0.0 --port ${PORT:-7860}"]
