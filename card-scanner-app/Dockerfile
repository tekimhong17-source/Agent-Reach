FROM python:3.12-slim

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY backend ./backend
COPY frontend ./frontend

# Database lives on a mounted persistent disk in production:
# set CARDVAULT_DB=/data/cardvault.db and mount a volume at /data.
RUN mkdir -p /data

EXPOSE 8000
CMD ["sh", "-c", "uvicorn backend.main:app --host 0.0.0.0 --port ${PORT:-8000}"]
