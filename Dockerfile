# Use an official Python runtime as a parent image
FROM python:3.12.11-slim

# Set the working directory in the container to /opt/kidde-collector
WORKDIR /app/kidde-collector

RUN apt-get update && apt-get install -y

# Copy the required files to the working directory
COPY requirements.txt ./src/config.py ./src/influxdb_writer.py ./src/mqtt_writer.py ./src/kidde_api.py ./src/kidde_collector.py ./src/kidde_homesafe.py ./

# Upgrade pip and install required packages
RUN pip install --upgrade pip && \
    pip install --no-cache-dir -r requirements.txt

HEALTHCHECK --interval=30s --timeout=10s --start-period=10s --retries=3 \
  CMD /bin/sh -c 'test -f health/last_successful_cycle && [ $(($(date +%s) - $(stat -c %Y health/last_successful_cycle))) -lt 240 ]'

# Run main.py when the container launches
CMD ["python3", "./kidde_collector.py"]
