# Fake Kidde cloud emulator image (dev/demo/e2e harness). Pure stdlib — no deps.
FROM python:3.14-slim
COPY fake_kidde.py /fake_kidde.py
# Serves the cookie-session login + location/device REST API on one HTTP port.
# Internal to the stack.
EXPOSE 8080/tcp
CMD ["python3", "/fake_kidde.py"]
