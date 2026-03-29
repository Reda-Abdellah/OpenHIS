FROM python:3.11-slim
RUN apt-get update && apt-get install -y --no-install-recommends curl && rm -rf /var/lib/apt/lists/*
WORKDIR /app
COPY libs/openhis_sdk /openhis_sdk
RUN pip install --no-cache-dir -e /openhis_sdk
COPY services/ris/requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY services/ris .
RUN mkdir -p /data
EXPOSE 8002
HEALTHCHECK --interval=20s --timeout=5s --start-period=10s --retries=3 \
    CMD curl -f http://localhost:8002/api/health || exit 1
CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8002"]
