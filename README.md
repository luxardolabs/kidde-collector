


## About The Project

![enter image description here](https://labs.lux4rd0.com/wp-content/uploads/2024/06/kidde_collector-by_device.jpg)

**Kidde Collector** is a Python application deployed with Docker designed to efficiently collect data from the Kidde HomeSafe system. Once deployed, Kidde Collector provides a comprehensive set of Grafana dashboards, enabling you to visualize and effortlessly analyze your Kidde HomeSafe data in real time. Whether starting with Grafana, InfluxDB, and Kidde HomeSafe or looking to enhance your existing setup, Kidde Collector offers an intuitive and powerful solution for monitoring and understanding your Kidde HomeSafe data.

## Getting Started

The project is deployed as a Docker container.

## Prerequisites

- [Docker](https://docs.docker.com/install)
- [Docker Compose](https://docs.docker.com/compose/install)
- [InfluxDB 2.x](https://docs.influxdata.com/influxdb/v2/)
- [Grafana](https://grafana.com/oss/grafana/)

## Deploying the Kidde Collector

Use the following [Docker container](https://hub.docker.com/r/lux4rd0/kidde-collector):

    lux4rd0/kidde-collector:latest

Correct environmental variables are required for the container to function.

      KIDDE_COLLECTOR_INFLUXDB_BUCKET
      KIDDE_COLLECTOR_INFLUXDB_ORG
      KIDDE_COLLECTOR_INFLUXDB_TOKEN
      KIDDE_COLLECTOR_INFLUXDB_URL
      KIDDE_COLLECTOR_KIDDE_PASSWORD
      KIDDE_COLLECTOR_KIDDE_USERNAME

An example command line would be (be sure to change all of the variables):

To start the docker container, simply update this minimal example `docker-compose.yml` file:

    name: kidde-collector
    services:
      kidde-collector:
        container_name: kidde-collector
        environment:
          KIDDE_COLLECTOR_INFLUXDB_BUCKET: kidde
          KIDDE_COLLECTOR_INFLUXDB_ORG: Lux4rd0
          KIDDE_COLLECTOR_INFLUXDB_TOKEN: TOKEN
          KIDDE_COLLECTOR_INFLUXDB_URL: http://kiddie-collector.lux4rd0.com:8086
          KIDDE_COLLECTOR_KIDDE_PASSWORD: PASSWORD
          KIDDE_COLLECTOR_KIDDE_USERNAME: dave@lux4rd0.com
        image: lux4rd0/kidde-collector:latest
        restart: always

If you don't want to use docker-compose, an example docker run command will be displayed on the screen.

    docker run --rm \
      --name=kidde-collector \
      -e KIDDE_COLLECTOR_INFLUXDB_BUCKET=kidde \
      -e KIDDE_COLLECTOR_INFLUXDB_ORG=Lux4rd0 \
      -e KIDDE_COLLECTOR_INFLUXDB_TOKEN=TOKEN \
      -e KIDDE_COLLECTOR_INFLUXDB_URL=http://kiddie-collector.lux4rd0.com:8086 \
      -e KIDDE_COLLECTOR_KIDDE_PASSWORD=PASSWORD \
      -e KIDDE_COLLECTOR_KIDDE_USERNAME=dave@lux4rd0.com \
      --restart always \
      lux4rd0/kidde-collector:latest

Running `docker compose up -d` or the `docker run` command will download and start up the kidde-collector container.

## Environmental Flags:

The Docker container can be configured with additional environment flags to control collector behaviors. They are described below:

### KIDDE_COLLECTOR_API_DATA_FOLDER
- **Description**: The directory where API data will be stored.
- **Required**: No
- **Default**: export

### KIDDE_COLLECTOR_COOKIES_DIR
- **Description**: The directory where cookie files will be stored.
- **Required**: No
- **Default**: cookies

### KIDDE_COLLECTOR_FETCH_INTERVAL_SECONDS
- **Description**: The interval in seconds between data fetches.
- **Required**: No
- **Default**: 60

### KIDDE_COLLECTOR_INFLUXDB_BUCKET
- **Description**: The bucket name in InfluxDB where data will be stored.
- **Required**: Yes

### KIDDE_COLLECTOR_INFLUXDB_ORG
- **Description**: The organization name in InfluxDB.
- **Required**: Yes

### KIDDE_COLLECTOR_INFLUXDB_TOKEN
- **Description**: The authentication token for InfluxDB.
- **Required**: Yes

### KIDDE_COLLECTOR_INFLUXDB_URL
- **Description**: The URL of the InfluxDB instance where data will be written.
- **Required**: Yes

### KIDDE_COLLECTOR_KIDDE_PASSWORD
- **Description**: The password for authenticating with the Kidde API.
- **Required**: Yes

### KIDDE_COLLECTOR_KIDDE_USERNAME
- **Description**: The username (email address) for authenticating with the Kidde API.
- **Required**: Yes

### KIDDE_COLLECTOR_LOG_LEVEL
- **Description**: Sets the general logging level for the application.
- **Required**: No
- **Default**: INFO
- **Options**: DEBUG, INFO, WARNING, ERROR, CRITICAL

### KIDDE_COLLECTOR_WRITE_API_DATA
- **Description**: Enables or disables the output of received Kidde API data to a file.
- **Required**: No
- **Default**: false
- **Options**: true, false

## Contact

Dave Schmid: [@lux4rd0](https://twitter.com/lux4rd0) - dave@pulpfree.org

Project Link: https://github.com/lux4rd0/kidde-collector

## Acknowledgements

- Grafana Labs - [https://grafana.com/](https://grafana.com/)
- Grafana - [https://grafana.com/oss/grafana/](https://grafana.com/oss/grafana/)
- Grafana Dashboard Community - [https://grafana.com/grafana/dashboards/](https://grafana.com/grafana/dashboards/)

