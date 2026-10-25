FROM python:3.11-slim

WORKDIR /app

# Install system dependencies if required (e.g. for psycopg2)

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

# Ensure env variables for Docker
ENV PYTHONUNBUFFERED=1

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
