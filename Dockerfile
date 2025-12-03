FROM python:3.10-slim

WORKDIR /app

# Install system dependencies
# build-essential for compiling, libfreetype6-dev/libpng-dev for matplotlib
RUN apt-get update && apt-get install -y \
    build-essential \
    libfreetype6-dev \
    libpng-dev \
    pkg-config \
    && rm -rf /var/lib/apt/lists/*

# Copy requirements first to leverage cache
COPY requirements.txt .
# Force reinstall dependencies (Updated: 2025-12-03)
RUN pip install --no-cache-dir -r requirements.txt

# Copy the rest of the application
COPY . .

# Ensure start script is executable
RUN chmod +x start.sh

# Create data directory if it doesn't exist
RUN mkdir -p data

# Expose the port the app runs on (5001 as requested)
EXPOSE 5001

# Command to run the application
CMD ["./start.sh"]
