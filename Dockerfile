# Use official Playwright Python image
FROM mcr.microsoft.com/playwright/python:v1.42.0-jammy

# Set working directory
WORKDIR /app

# Install Python dependencies first (caching layer)
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Install Chromium
RUN playwright install chromium

# Copy application code
COPY . .

# Don't buffer Python output (important for Docker logs)
ENV PYTHONUNBUFFERED=1

# Health check — verifies the process is running
HEALTHCHECK --interval=60s --timeout=10s --retries=3 \
    CMD python -c "import requests; requests.get('https://api.telegram.org', timeout=5)" || exit 1

# Run the bot
CMD ["python", "theater_automation.py"]
