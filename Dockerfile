FROM python:3.12-slim

WORKDIR /app

# Install dependencies
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy source
COPY . .

# Cloud Run Jobs: run once and exit
ENV DEPLOYMENT_ENV=gcp

CMD ["python", "main.py"]
