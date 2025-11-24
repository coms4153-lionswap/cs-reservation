FROM python:3.11-slim

# Make Python output unbuffered (so logs appear immediately)
ENV PYTHONUNBUFFERED=1

# Set working directory
WORKDIR /app

# Install system dependencies (needed for psycopg2 / PostgreSQL, etc.)
RUN apt-get update && apt-get install -y \
    build-essential \
    libpq-dev \
  && rm -rf /var/lib/apt/lists/*

# Copy dependency list and install
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy the rest of your app code
COPY . .

# Cloud Run sends traffic to the port in the $PORT env var (default to 8080)
ENV PORT=8080

# Start FastAPI with Uvicorn
# We ignore the __main__ block in main.py and start uvicorn directly
CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8080"]
