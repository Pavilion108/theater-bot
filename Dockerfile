# Lightweight Python image
FROM python:3.12-slim

# Set working directory
WORKDIR /app

# Install dependencies first (caching layer)
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy application code
COPY theater_automation.py .

# Don't buffer Python output (important for Docker logs)
ENV PYTHONUNBUFFERED=1

# Health check — verifies the process is running
HEALTHCHECK --interval=60s --timeout=10s --retries=3 \
    CMD python -c "import requests; requests.get('https://api.telegram.org', timeout=5)" || exit 1

# Run the bot
CMD ["python", "theater_automation.py"]
