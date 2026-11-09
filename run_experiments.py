"""
Runs the experiments automatically, one after another, and draws the plot at the end.

Usage (from the repository folder, with the services already started: cd compose && docker compose up -d):
    python run_experiments.py             -> midterm set: 50 devices, no-chain and anchoring, seeds 1-5 (10 runs, ~25 min)
    python run_experiments.py --quick     -> one run per mode, seed 1 (2 runs, ~5 min)

To keep the laptop awake while it runs (it goes back to normal afterwards):
    systemd-inhibit --what=sleep:idle:handle-lid-switch --why="experiments" python run_experiments.py

Every run:
    1. starts the gateway in the right mode (it writes its logs into the run folder),
    2. starts the sensors container with the right seed (netem is switched on inside it),
    3. lets everything run for RUN_SECONDS,
    4. stops the sensors, waits a few seconds so the last batches get confirmed, stops the gateway.
All logs of one call of this script go into one folder: results/experiments_<date>_<time>/
"""
import os
import sys
import time
import signal
import subprocess

RUN_SECONDS = int(os.environ.get("RUN_SECONDS", "120"))   # length of one run (the PDF: 120 s)
DRAIN_SECONDS = 5        # after the sensors stop: time for the last batches to be confirmed
GATEWAY_START_SECONDS = 3
DEVICES = 50
MODES = ["nochain", "anchoring"]
SEEDS = [1, 2, 3, 4, 5]

PUBLIC_KEYS_FILE = "edge/keys/public_keys.json"
DEPLOYMENT_FILE = "chain/deployment.json"
NEEDED_SERVICES = ["mosquitto", "influxdb", "besu-node1", "besu-node2", "besu-node3", "besu-node4"]


def running_services():
    result = subprocess.run(["docker", "compose", "ps", "--status", "running", "--services"],
                            cwd="compose", capture_output=True, text=True)
    return result.stdout.split()


def check_ready():
    """Stop early, with a clear message, if something needed is missing."""
    running = running_services()
    missing = []
    for service in NEEDED_SERVICES:
        if service not in running:
            missing.append(service)
    if len(missing) > 0:
        print(f"These services are not running: {', '.join(missing)}")
        print("Start them first:  cd compose && docker compose up -d")
        sys.exit(1)
    if "anchoring" in MODES and not os.path.exists(DEPLOYMENT_FILE):
        print("The Anchor contract is not deployed. Run:  npx hardhat compile && python chain/deploy.py")
        sys.exit(1)


def docker_compose(arguments, extra_env=None):
    env = os.environ.copy()
    if extra_env is not None:
        env.update(extra_env)
    subprocess.run(["docker", "compose"] + arguments, cwd="compose", env=env,
                   stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, check=True)


def run_one(folder, mode, seed):
    """One run: gateway + sensors for RUN_SECONDS."""
    tag = f"seed{seed}"

    # Each seed gives the sensors different keys. Removing the old public keys file makes the
    # gateway read the new one as soon as the new sensors write it.
    if os.path.exists(PUBLIC_KEYS_FILE):
        os.remove(PUBLIC_KEYS_FILE)

    # 1. Gateway, with its screen output saved to a file
    gateway_env = os.environ.copy()
    gateway_env["RESULTS_DIR"] = folder
    gateway_env["RUN_TAG"] = tag
    gateway_output = open(f"{folder}/gateway_{mode}_{tag}.log", "w")
    gateway = subprocess.Popen([sys.executable, "-u", "gateway/gateway.py", mode],
                               env=gateway_env, stdout=gateway_output, stderr=subprocess.STDOUT)
    time.sleep(GATEWAY_START_SECONDS)
    if gateway.poll() is not None:
        print(f"The gateway stopped right away, see {folder}/gateway_{mode}_{tag}.log")
        sys.exit(1)

    # 2. Sensors: a fresh container every run, so netem and the seed are set from scratch
    docker_compose(["up", "-d", "--force-recreate", "edge"], {"SEED": str(seed), "DEVICES": str(DEVICES)})

    # 3. Let it run
    time.sleep(RUN_SECONDS)

    # 4. Stop the sensors, let the last batches finish, stop the gateway (like pressing Ctrl+C)
    docker_compose(["stop", "edge"])
    time.sleep(DRAIN_SECONDS)
    gateway.send_signal(signal.SIGINT)
    gateway.wait(timeout=30)
    gateway_output.close()


def main():
    global SEEDS
    if "--quick" in sys.argv:
        SEEDS = [1]

    check_ready()

    folder = f"results/experiments_{time.strftime('%Y%m%d_%H%M%S')}"
    os.makedirs(folder, exist_ok=True)

    total = len(SEEDS) * len(MODES)
    minutes = total * (RUN_SECONDS + DRAIN_SECONDS + GATEWAY_START_SECONDS + 10) / 60
    print(f"{total} runs of {RUN_SECONDS} s, about {minutes:.0f} minutes. Logs in {folder}/")

    # Seed by seed, both modes for each seed: if anything drifts during the evening,
    # it affects both modes the same way
    number = 0
    for seed in SEEDS:
        for mode in MODES:
            number = number + 1
            print(f"[{time.strftime('%H:%M:%S')}] run {number}/{total}: {mode}, seed {seed}, {DEVICES} devices")
            run_one(folder, mode, seed)

    print(f"[{time.strftime('%H:%M:%S')}] all runs done, drawing the plot")
    log_files = []
    for name in sorted(os.listdir(folder)):
        if name.endswith(".csv"):
            log_files.append(f"{folder}/{name}")
    subprocess.run([sys.executable, "analysis/plot_latency.py"] + log_files)


main()
