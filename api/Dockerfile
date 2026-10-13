FROM python:3.12-slim

WORKDIR /app

ENV PYTHONUNBUFFERED=1

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . /app/api/

EXPOSE 8000

# Single worker is deliberate: POST /api/run's gate check-then-write is only
# race-free within one uvicorn worker (see api/main.py post_run); scaling out
# requires a real lock.
CMD ["uvicorn", "api.main:app", "--host", "0.0.0.0", "--port", "8000", "--workers", "1"]
