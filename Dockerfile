FROM python:3.11-slim

WORKDIR /app

# Install system dependencies
RUN apt-get update && apt-get install -y \
    curl \
    && rm -rf /var/lib/apt/lists/*

# Copy requirements first for better caching
COPY requirements.txt .

# Install Python dependencies
RUN pip install --no-cache-dir -r requirements.txt

# Copy application code
COPY . .

# Expose port 5000 (Flask default)
EXPOSE 5000

# Set environment variables
ENV FLASK_APP=app/app.py
ENV PYTHONUNBUFFERED=1
ENV FLASK_ENV=production

# Run the Flask app
CMD ["python", "app/app.py"]
