FROM python:3.11-slim
WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY run.py .
ENV JOBS_DATA_DIR=/data/jobs
CMD ["python", "run.py"]
