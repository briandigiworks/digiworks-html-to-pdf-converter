FROM mcr.microsoft.com/playwright/python:v1.47.0-noble

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY main.py .

EXPOSE 8000

CMD uvicorn main:app --host 0.0.0.0 --port ${PORT:-8000} --workers 1
