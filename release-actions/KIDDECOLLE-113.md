# KIDDECOLLE-113: container logs are capped

Heads-up for the release notes. Every service in `compose.yml` now logs through one shared block: by default the `json-file` driver, at most 5 files of 10 MB per container. This replaces whatever log driver the host's Docker daemon defaults to.

- **A daemon that ships to Loki** keeps doing so only when its `.env.<env>` sets `LOG_DRIVER` to exactly the daemon's own log-driver value (`docker info --format '{{.LoggingDriver}}'`, e.g. `loki`). The daemon then adds its Loki URL beneath the caps.
- **journald, syslog, fluentd or gelf** refuse the caps, so `LOG_DRIVER` cannot name them. On such a host the stack logs to capped json-file.
- `LOG_MAX_SIZE` and `LOG_MAX_FILE` change the caps.

The change takes effect when the containers are recreated (`docker compose up -d`). No image change is needed.
