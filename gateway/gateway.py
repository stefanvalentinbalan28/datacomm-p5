"""
The gateway: receives the sensor messages from Mosquitto, checks them, stores them in InfluxDB
and (in anchoring mode) puts a Merkle root of every 500 ms batch on the blockchain.

Usage (from the repository folder):
    python gateway/gateway.py              -> mode "nochain"   (no blockchain, the reference)
    python gateway/gateway.py anchoring    -> mode "anchoring" (batches + Merkle root on chain)

How the work is split between threads (parts of the program that run at the same time):
    MQTT thread  - receives a message, notes t_gw and puts it in the queue. Nothing else.
    worker       - takes messages from the queue: verifies, stores in InfluxDB, adds to the batch.
    main loop    - every 500 ms closes the batch; every second writes the gateway stats.
    anchor jobs  - one per batch: sends the root to the blockchain and waits for confirmation.
The queue between the MQTT thread and the worker is the gateway BACKLOG we have to measure.
"""
import os
import sys
import csv
import json
import time
import queue
import threading
import paho.mqtt.client as mqtt
from nacl.signing import VerifyKey
from nacl.exceptions import BadSignatureError
from influxdb_client import InfluxDBClient, Point, WritePrecision
from influxdb_client.client.write_api import SYNCHRONOUS

import merkle
import anchor_client

# ---------- Settings ----------

MODE = "nochain"
if len(sys.argv) > 1:
    MODE = sys.argv[1]
if MODE not in ["nochain", "anchoring"]:
    print("Mode must be 'nochain' or 'anchoring'")
    sys.exit(1)

# Batch window Δ, default 500 ms; the matrix also needs 250 and 1000:
#    BATCH_MS=250 python gateway/gateway.py anchoring
BATCH_SECONDS = int(os.environ.get("BATCH_MS", "500")) / 1000
MAX_AGE_SECONDS = 5      # older messages are rejected

MQTT_HOST = os.environ.get("MQTT_HOST", "127.0.0.1")

# The sensors' PUBLIC keys (written by edge/main.py). With a public key we can only
# VERIFY a signature, not create one -> the gateway cannot forge sensor messages.
PUBLIC_KEYS_FILE = "edge/keys/public_keys.json"
public_keys = {}

# InfluxDB connection settings (must match compose/docker-compose.yml)
INFLUX_URL = os.environ.get("INFLUX_URL", "http://127.0.0.1:8086")
INFLUX_TOKEN = "token_secret_proiect5"
INFLUX_ORG = "proiect5"
INFLUX_BUCKET = "iot_data"

influx_client = InfluxDBClient(url=INFLUX_URL, token=INFLUX_TOKEN, org=INFLUX_ORG)
write_api = influx_client.write_api(write_options=SYNCHRONOUS)

# ---------- Log files (all in results/, same name start for one run) ----------
#   results/anchoring_20261105_101500.csv          one row per message
#   results/anchoring_20261105_101500_batches.csv  one row per batch (anchoring only)
#   results/anchoring_20261105_101500_gateway.csv  one row per second: backlog and CPU

# run_experiments.py can choose the folder and add a tag (e.g. the seed) to the names
RESULTS_DIR = os.environ.get("RESULTS_DIR", "results")
RUN_TAG = os.environ.get("RUN_TAG", "")

os.makedirs(RESULTS_DIR, exist_ok=True)
run_name = f"{RESULTS_DIR}/{MODE}_{time.strftime('%Y%m%d_%H%M%S')}"
if RUN_TAG != "":
    run_name = run_name + "_" + RUN_TAG

message_file = open(run_name + ".csv", "w", newline="")
message_log = csv.writer(message_file)
message_log.writerow(["device_id", "type", "seq", "size_bytes", "msg_bytes",
                      "t_pub", "t_gw", "t_db", "verify_us", "status", "batch_id"])

batch_file = None
batch_log = None
batch_log_lock = threading.Lock()   # several anchor jobs may write at the same time
if MODE == "anchoring":
    batch_file = open(run_name + "_batches.csv", "w", newline="")
    batch_log = csv.writer(batch_file)
    batch_log.writerow(["batch_id", "count", "root", "proof_bytes",
                        "t_batch_close", "t_tx_sent", "t_confirmed", "block_number", "status"])

stats_file = open(run_name + "_gateway.csv", "w", newline="")
stats_log = csv.writer(stats_file)
stats_log.writerow(["t", "backlog", "cpu_percent", "saved", "rejected"])

# ---------- Shared state ----------

incoming = queue.Queue()      # messages waiting for the worker = the backlog

current_batch = []            # messages of the batch that is still open
current_batch_id = 1
batch_lock = threading.Lock()  # the worker adds to the batch while the main loop closes it

totals = {"saved": 0, "rejected": 0}


# ---------- Signatures ----------

def load_public_keys():
    """(Re)read the public keys file. Called again when an unknown sensor shows up,
    because the sensors may have been started after the gateway."""
    global public_keys
    if os.path.exists(PUBLIC_KEYS_FILE):
        with open(PUBLIC_KEYS_FILE) as f:
            public_keys = json.load(f)


def signature_is_valid(message, received_sig):
    """True if the signature was made with the private key of the sensor named in the message."""
    device_id = message.get("device_id")
    if device_id not in public_keys:
        load_public_keys()
    if device_id not in public_keys:
        return False  # unknown sensor

    verify_key = VerifyKey(bytes.fromhex(public_keys[device_id]))
    signed_text = json.dumps(message, sort_keys=True).encode('utf-8')
    try:
        verify_key.verify(signed_text, bytes.fromhex(received_sig))
        return True
    except (BadSignatureError, ValueError):
        return False  # wrong signature, or "sig" is not even valid hex text


# ---------- Receiving (MQTT thread) ----------

def on_mqtt_message(client, userdata, msg):
    """Runs for every message. Only notes the arrival time and queues the message,
    so receiving never waits for the database or the blockchain."""
    t_gw = time.time()
    incoming.put([t_gw, msg.topic, msg.payload])


def on_mqtt_connect(client, userdata, flags, reason_code, properties):
    """Runs after every (re)connection to Mosquitto. Subscribing here, and not only once at
    the start, means the gateway keeps receiving even if Mosquitto is restarted."""
    client.subscribe("sensors/#")  # # = every topic under sensors/
    print("Connected to Mosquitto, subscribed to sensors/#")


# ---------- Processing (worker thread) ----------

def write_message_log(message, sensor_type, msg_bytes, t_gw, t_db, verify_us, status, batch_id):
    # size_bytes = payload only (200/300/500), msg_bytes = whole MQTT message (for the load in kbps)
    message_log.writerow([message.get("device_id"), sensor_type, message.get("seq"),
                          len(message.get("payload", "")), msg_bytes, message.get("ts_device"),
                          t_gw, t_db, round(verify_us, 1), status, batch_id])
    message_file.flush()  # write to disk immediately, so nothing is lost on Ctrl+C


def process_message(t_gw, topic, raw_payload):
    sensor_type = topic.split("/")[-1]    # "sensors/traffic" -> "traffic"
    msg_bytes = len(raw_payload)
    message = json.loads(raw_payload.decode('utf-8'))

    # Take the signature out of the message (empty text if the message has no signature)
    received_sig = message.pop("sig", "")

    # 1. Verify the signature, and measure how long that takes (KPI: µs per message)
    verify_start = time.perf_counter()
    sig_ok = signature_is_valid(message, received_sig)
    verify_us = (time.perf_counter() - verify_start) * 1_000_000

    if not sig_ok:
        print(f"invalid signature from {message.get('device_id')}: rejected")
        write_message_log(message, sensor_type, msg_bytes, t_gw, "", verify_us, "invalid_signature", "")
        totals["rejected"] += 1
        return

    # 2. Reject messages that are too old (protection against replaying old messages)
    if t_gw - message["ts_device"] > MAX_AGE_SECONDS:
        print(f"message from {message['device_id']} older than {MAX_AGE_SECONDS} s: rejected")
        write_message_log(message, sensor_type, msg_bytes, t_gw, "", verify_us, "too_old", "")
        totals["rejected"] += 1
        return

    # 3. Store the FULL original record (raw record) in InfluxDB
    point_time_ns = time.time_ns()   # remembered: the Merkle proof is added to this same point later
    point = Point("sensor_measurements") \
        .tag("device_id", message["device_id"]) \
        .tag("type", sensor_type) \
        .field("seq", message["seq"]) \
        .field("ts_device", message["ts_device"]) \
        .field("payload", message["payload"]) \
        .field("sig", received_sig) \
        .field("size_bytes", len(message["payload"])) \
        .time(point_time_ns, WritePrecision.NS)
    write_api.write(bucket=INFLUX_BUCKET, org=INFLUX_ORG, record=point)
    t_db = time.time()               # moment InfluxDB confirmed the write

    # 4. Anchoring mode: the message joins the batch that is open right now
    batch_id = ""
    if MODE == "anchoring":
        message["sig"] = received_sig   # the record in the tree is the full message, with signature
        with batch_lock:
            current_batch.append({"record": message, "type": sensor_type,
                                  "point_time_ns": point_time_ns})
            batch_id = current_batch_id

    write_message_log(message, sensor_type, msg_bytes, t_gw, t_db, verify_us, "ok", batch_id)
    totals["saved"] += 1


def worker_loop():
    while True:
        t_gw, topic, raw_payload = incoming.get()   # waits until there is a message
        try:
            process_message(t_gw, topic, raw_payload)
        except Exception as error:
            print(f"could not process a message: {error}")


# ---------- Batches and anchoring (main loop + anchor jobs) ----------

def close_batch():
    """Close the open batch, build its Merkle tree, store the proofs, start anchoring."""
    global current_batch, current_batch_id

    with batch_lock:
        items = current_batch
        batch_id = current_batch_id
        current_batch = []
        current_batch_id = current_batch_id + 1
    t_batch_close = time.time()

    if len(items) == 0:
        return  # nothing arrived in these 500 ms

    # Merkle tree: one leaf per message, in the order they arrived
    leaves = []
    for item in items:
        leaves.append(merkle.hash_leaf(merkle.record_bytes(item["record"])))
    levels = merkle.build_tree(leaves)
    root = merkle.get_root(levels)

    # Add batch id and proof to each stored record: same measurement, tags and time
    # as the record itself, so InfluxDB adds these fields to the existing point
    points = []
    proof_bytes = 0
    for i in range(len(items)):
        proof = merkle.get_proof(levels, i)
        proof_bytes = max(proof_bytes, 32 * len(proof))   # largest proof in this batch
        point = Point("sensor_measurements") \
            .tag("device_id", items[i]["record"]["device_id"]) \
            .tag("type", items[i]["type"]) \
            .field("batch_id", batch_id) \
            .field("merkle_proof", json.dumps(proof)) \
            .time(items[i]["point_time_ns"], WritePrecision.NS)
        points.append(point)
    write_api.write(bucket=INFLUX_BUCKET, org=INFLUX_ORG, record=points)

    # Send the root to the blockchain in the background: waiting for a block takes
    # about a second, and meanwhile the next batches must keep closing on time
    job = threading.Thread(target=anchor_batch,
                           args=(batch_id, root, len(items), t_batch_close, proof_bytes),
                           daemon=True)
    job.start()


def anchor_batch(batch_id, root, count, t_batch_close, proof_bytes):
    try:
        t_tx_sent, t_confirmed, block_number, status = \
            anchor_client.send_anchor(root, count, int(t_batch_close * 1000))
    except Exception as error:
        t_tx_sent, t_confirmed, block_number, status = time.time(), None, None, f"error: {error}"

    with batch_log_lock:
        batch_log.writerow([batch_id, count, root.hex(), proof_bytes,
                            t_batch_close, t_tx_sent, t_confirmed, block_number, status])
        batch_file.flush()

    if status == "confirmed":
        print(f"batch {batch_id}: {count} messages, root {root.hex()[:12]}... confirmed in block {block_number}")


# ---------- Gateway stats (main loop) ----------

last_stats = {"wall": time.time(), "cpu": time.process_time(), "saved": 0, "rejected": 0}


def write_stats():
    """Once per second: backlog, CPU used by the gateway, messages saved and rejected."""
    now = time.time()
    cpu_now = time.process_time()   # CPU seconds used by this program (all threads together)
    cpu_percent = 100 * (cpu_now - last_stats["cpu"]) / (now - last_stats["wall"])

    saved = totals["saved"] - last_stats["saved"]
    rejected = totals["rejected"] - last_stats["rejected"]
    backlog = incoming.qsize()

    stats_log.writerow([now, backlog, round(cpu_percent, 1), saved, rejected])
    stats_file.flush()
    print(f"{saved} saved, {rejected} rejected, backlog {backlog}, CPU {cpu_percent:.0f}%")

    last_stats["wall"] = now
    last_stats["cpu"] = cpu_now
    last_stats["saved"] = totals["saved"]
    last_stats["rejected"] = totals["rejected"]


# ---------- Start ----------

load_public_keys()

mqtt_client = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2)
mqtt_client.on_connect = on_mqtt_connect
mqtt_client.on_message = on_mqtt_message
mqtt_client.connect(MQTT_HOST, 1883)
mqtt_client.loop_start()            # MQTT thread starts: connects, subscribes, receives

worker = threading.Thread(target=worker_loop, daemon=True)
worker.start()

print(f"Gateway running in mode '{MODE}', batch window {BATCH_SECONDS * 1000:.0f} ms. "
      f"Logs: {run_name}*.csv. Waiting for data...")
if MODE == "anchoring" and not anchor_client.CHAIN_READY:
    print("blockchain client not ready yet (package C): batches are built but not sent")

next_batch_close = time.time() + BATCH_SECONDS
next_stats = time.time() + 1
try:
    while True:
        time.sleep(0.01)
        now = time.time()
        if MODE == "anchoring" and now >= next_batch_close:
            close_batch()
            next_batch_close = next_batch_close + BATCH_SECONDS
        if now >= next_stats:
            write_stats()
            next_stats = next_stats + 1
except KeyboardInterrupt:
    print("\nStopped.")
finally:
    mqtt_client.loop_stop()
    message_file.close()
    stats_file.close()
    if batch_file is not None:
        batch_file.close()
