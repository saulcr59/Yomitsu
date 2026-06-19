FROM python:3.12-slim
WORKDIR /app
RUN apt-get update -qq && \
    apt-get install -y -qq --no-install-recommends gcc liblzo2-dev python3-dev && \
    rm -rf /var/lib/apt/lists/*
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY *.py .
CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8000"]
