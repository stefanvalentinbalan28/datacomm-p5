# Container for the simulated sensors (edge/main.py)
FROM python:3.12-slim

# iproute2 contains the "tc" command, used for netem (delay, jitter, loss)
RUN apt-get update && apt-get install -y --no-install-recommends iproute2 && rm -rf /var/lib/apt/lists/*

# The Python libraries the sensors need
RUN pip install --no-cache-dir paho-mqtt pynacl

# Same folder layout as the repository, so the paths in main.py stay the same
WORKDIR /app
COPY main.py edge/main.py
