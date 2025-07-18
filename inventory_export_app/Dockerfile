FROM python:3.9-slim

WORKDIR /app

# Install system dependencies
RUN apt-get update && \
    apt-get install -y gcc libssl-dev g++ && \
    rm -rf /var/lib/apt/lists/*  # No space after backslash!

# Copy requirements first to leverage Docker cache
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy application files
COPY . .

# Expose port and run application
EXPOSE 5005
CMD ["python", "app.py"]