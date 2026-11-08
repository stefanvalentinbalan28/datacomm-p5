import os
import time
import json
import hashlib
import random
import paho.mqtt.client as mqtt
from nacl.signing import SigningKey

# Run settings, given from outside (see compose/docker-compose.yml); defaults: seed 1, 50 sensors.
# The same seed gives the same sensor schedule and the same keys -> runs are comparable.
SEED = int(os.environ.get("SEED", "1"))
DEVICES = int(os.environ.get("DEVICES", "50"))   # 50 or 200 (the two scales of the matrix)
random.seed(SEED)

# Every sensor has its own Ed25519 key pair. The private key stays here, in the sensor program.
# The public keys are written to this file, so the gateway can verify the signatures.
PUBLIC_KEYS_FILE = "edge/keys/public_keys.json"

# Where the MQTT broker (Mosquitto) is. Inside Docker the broker is found by its service
# name "mosquitto"; when the script runs directly on the laptop it is 127.0.0.1.
MQTT_HOST = os.environ.get("MQTT_HOST", "127.0.0.1")

# The 4 sensor classes from the requirements: how many sensors of each per 50 devices,
# and the payload size. With 200 devices every count is multiplied by 4.
CLASSES = {
    "traffic":   {"count": 30, "size": 200},
    "air":       {"count": 10, "size": 300},
    "vibration": {"count": 5,  "size": 300},  # 10% of the devices
    "alert":     {"count": 5,  "size": 500},
}
SCALE = DEVICES // 50

TRAFFIC_PERIOD = 0.5      # 2 Hz   -> one message every 0.5 s
AIR_PERIOD = 2.0          # 0.5 Hz -> one message every 2 s
VIBRATION_PERIOD = 0.05   # 20 Hz  -> one message every 0.05 s, but only during a burst
BURST_DURATION = 1.0      # a vibration burst lasts 1 s...
BURST_PAUSE = 1.0         # ...followed by 1 s of silence (chosen so the total never drops below 100 msg/s at 50 sensors)
VIBRATION_CYCLE = BURST_DURATION + BURST_PAUSE
ALERT_MEAN_INTERVAL = 30.0  # alerts are rare: on average one every 30 s, at random moments

TICK = 0.01               # every 10 ms we check which sensors have to send


def make_private_key(device_id):
    """Each sensor's private key is derived from the run seed and its name,
    so the same sensor gets the same key in every run with the same seed."""
    key_seed = hashlib.sha256(f"{SEED}:{device_id}".encode('utf-8')).digest()  # 32 bytes
    return SigningKey(key_seed)


def save_public_keys(sensors):
    """Write every sensor's PUBLIC key to a file that the gateway reads."""
    public_keys = {}
    for sensor in sensors:
        public_key = sensor["private_key"].verify_key
        public_keys[sensor["id"]] = public_key.encode().hex()
    os.makedirs("edge/keys", exist_ok=True)
    with open(PUBLIC_KEYS_FILE, "w") as f:
        json.dump(public_keys, f, indent=1)


def build_message(sensor):
    message = {
        "device_id": sensor["id"],
        "ts_device": time.time(),  # seconds WITH decimals -> we can measure milliseconds
        "seq": sensor["seq"],
        "payload": "X" * sensor["size"]
    }
    # Sign the message with this sensor's own private key (Ed25519)
    signed_text = json.dumps(message, sort_keys=True).encode('utf-8')
    signature = sensor["private_key"].sign(signed_text).signature  # 64 bytes
    message["sig"] = signature.hex()                                # 128 hex characters
    return json.dumps(message)


def create_sensors(now):
    sensors = []
    for sensor_type, settings in CLASSES.items():
        count = settings["count"] * SCALE
        for i in range(1, count + 1):
            sensor_id = f"{sensor_type}_{i}"
            sensor = {"id": sensor_id, "type": sensor_type, "size": settings["size"], "seq": 1,
                      "private_key": make_private_key(sensor_id)}

            # When does each sensor send its first message
            if sensor_type == "traffic":
                # random start, so traffic sensors don't all send at the same moment
                sensor["next_send"] = now + random.uniform(0, TRAFFIC_PERIOD)
            elif sensor_type == "air":
                # air sensors are spread evenly over the 2 s -> the same number of messages every second
                sensor["next_send"] = now + (i - 1) * AIR_PERIOD / count
            elif sensor_type == "alert":
                sensor["next_send"] = now + random.expovariate(1 / ALERT_MEAN_INTERVAL)
            elif sensor_type == "vibration":
                # bursts are staggered evenly, so at any moment about half of them are bursting
                sensor["next_send"] = now + (i - 1) * VIBRATION_CYCLE / count
                sensor["burst_end"] = sensor["next_send"] + BURST_DURATION

            sensors.append(sensor)
    return sensors


def schedule_next(sensor):
    """Compute when the sensor has to send its next message."""
    sensor_type = sensor["type"]
    if sensor_type == "traffic":
        sensor["next_send"] += TRAFFIC_PERIOD
    elif sensor_type == "air":
        sensor["next_send"] += AIR_PERIOD
    elif sensor_type == "alert":
        sensor["next_send"] += random.expovariate(1 / ALERT_MEAN_INTERVAL)
    elif sensor_type == "vibration":
        sensor["next_send"] += VIBRATION_PERIOD
        if sensor["next_send"] >= sensor["burst_end"]:
            # the burst is over: the next one starts after the pause
            sensor["next_send"] = sensor["burst_end"] + BURST_PAUSE
            sensor["burst_end"] = sensor["next_send"] + BURST_DURATION


client = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2)
client.connect(MQTT_HOST, 1883)

# Start a background thread that keeps the connection alive
client.loop_start()

sensors = create_sensors(time.time())
save_public_keys(sensors)
print(f"Started: {len(sensors)} sensors, seed {SEED}, public keys written to {PUBLIC_KEYS_FILE}")

sent_this_second = 0
last_print = time.time()

while True:
    now = time.time()

    for sensor in sensors:
        if now >= sensor["next_send"]:
            final_message = build_message(sensor)
            # each class has its own topic: sensors/traffic, sensors/air, ...
            client.publish(f"sensors/{sensor['type']}", final_message)
            sensor["seq"] += 1
            sent_this_second += 1
            schedule_next(sensor)

    # Once per second, print how many messages were sent
    if now - last_print >= 1:
        print(f"{sent_this_second} messages sent in the last second")
        sent_this_second = 0
        last_print = now

    time.sleep(TICK)
