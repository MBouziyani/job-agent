FROM python:3.12-slim

WORKDIR /app

RUN apt-get update && apt-get install -y --no-install-recommends \
    gcc libffi-dev \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Install Playwright browsers + system deps for Wellfound scraping
RUN playwright install chromium \
    && playwright install-deps chromium \
    && rm -rf /var/lib/apt/lists/*

COPY . .

CMD ["python", "main.py"]
