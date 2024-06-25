# Use an official Python runtime as a parent image
FROM python:3.12.3-slim

# Set the working directory in the container to /opt/kidde-collector
WORKDIR /app/kidde-collector

RUN apt-get update && apt-get install -y

# Copy the required files to the working directory
COPY requirements.txt kidde-collector.py kidde_homesafe.py config.py ./

# Upgrade pip and install required packages
RUN pip install --upgrade pip && \
    pip install --no-cache-dir -r requirements.txt

# Run main.py when the container launches
CMD ["python3", "./kidde-collector.py"]
