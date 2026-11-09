# datacomm-p5

Project 5 — Blockchain-anchored IoT data over MQTT: integrity vs overhead.
Data Communications, 2026–2027. Team: Galea Andreea, Popescu Daniel, Bălan Ștefan.
Variant: Hyperledger Besu with QBFT, 4 validators.

Simulated IoT sensors sign their own messages and publish them over MQTT. A gateway verifies
every signature, stores the full record in InfluxDB and — in *anchoring* mode — closes a
batch every 500 ms, builds a Merkle tree over it and writes only the root on a private
blockchain. We then measure what that integrity costs, against the same pipeline without a
chain.

Roadmap: [`docs/Roadmap_midterm.md`](docs/Roadmap_midterm.md) (current) ·
[`docs/Plan_Project5.docx`](docs/Plan_Project5.docx) (week-4 original)

## Status — midterm

P0, P1 and P2 are done and measured. P3 (full-record mode, tamper test, the full core matrix)
and P4 (analysis and report) have not started; see the roadmap for what we cut and when it
comes back.

| Phase | State |
|---|---|
| P0 Foundations — Mosquitto, InfluxDB, Grafana, Besu devnet, one confirmed anchor from Python | done |
| P1 Baseline (no-chain) — 50 devices → gateway → InfluxDB, per-message log | done |
| P2 Anchoring — 500 ms batches, Merkle root on chain, batch log | done |
| P3 Full-record, core matrix, tamper test | not started (weeks 9–12) |
| P4 Analysis and report | not started (weeks 13–14) |

## How to run

One command, from a clean clone, inside WSL2 with Docker running:

```bash
bash start.sh
```

It brings up Mosquitto, InfluxDB, Grafana and the four Besu validators, waits for the chain
to produce blocks, deploys `Anchor.sol`, runs one no-chain and one anchoring run of 120 s at
50 devices, and opens the resulting plot. `RUN_SECONDS=60 bash start.sh` for a shorter demo.

For a live demo that keeps running until you stop it:

```bash
bash startinf.sh     # anchoring mode, 50 sensors; Ctrl+C or bash stop.sh to stop
```

That one needs nothing on the host but Docker: the gateway and the contract deploy run
in the `gateway` container too (`compose/docker-compose.yml`, profile `app`). `bash start.sh` and
`run_experiments.py` still use the host venv.

Grafana is at <http://localhost:3000> (dashboard `p5-live`), InfluxDB at
<http://localhost:8086> (`admin` / `parolasecreta`).

To redraw the figures from logs that already exist:

```bash
python analysis/plot_latency.py results/experiments_<timestamp>/*.csv
```

Nothing in `docs/figures/` is drawn by hand — both figures come out of that one script.

## KPI definitions

Each KPI is defined once, here, and every number in the report refers back to these.
All timestamps come from one machine, so they share one clock. The first **20 s** of every
run are discarded as warm-up. p99 is reported only when a run yields at least 1000 samples.

| KPI | Definition |
|---|---|
| **L1** — device → gateway | `t_gw − t_pub`, where `t_pub` is set by the sensor just before publishing and `t_gw` the instant the gateway's MQTT callback receives the message. Includes the netem impairment (20 ms delay, 5 ms jitter, 1 % loss) and the broker. |
| **L2** — gateway → InfluxDB | `t_db − t_gw`: signature verification plus the acknowledged write of the full record. |
| **L3** — batch close → on chain | `t_confirmed − t_batch_close`, per batch, not per message. Confirmation happens per block, so L3 averages at least half a block period; the four clusters in `docs/figures/midterm_l3_distribution.png` are the four 500 ms batch windows that fit in one 2 s block. |
| **End to end** | no-chain: `t_db − t_pub` (message stored). Anchoring: `t_confirmed − t_pub` (message stored *and* its batch root confirmed on chain). Reported as p50/p95 (p99 where sample count allows). |
| **Ingest throughput** | accepted messages per second at the gateway, over the measured window. Reported next to the offered load in kbps and the run duration. |
| **On-chain rate** | transactions per second confirmed, and messages anchored per second (= batch sizes summed over time). |
| **Gateway backlog** | length of the queue between the MQTT receive thread and the worker, sampled once a second; reported as mean and maximum. Failed and retried transactions are counted separately from the batch log. |
| **Signature verification cost** | microseconds of CPU spent verifying one Ed25519 signature, measured around the verify call, plus total gateway CPU per second. |
| **Packet loss** | missing messages, found as gaps in each sensor's own `seq` counter. |

## Midterm result

50 devices, seed 1, 120 s per run (100 s measured), identical traffic and seeds in both
modes. Source logs: `results/experiments_20261105_051721/` (gzipped CSVs, committed).
Figures: `docs/figures/midterm_latency.png`, `docs/figures/midterm_l3_distribution.png`.

| | no-chain | anchoring |
|---|---|---|
| messages / throughput | 11 757 · 117.6 msg/s · 447 kbps | 11 759 · 117.6 msg/s · 447 kbps |
| L1 p50 / p95 | 27.5 / 55.0 ms | 27.6 / 57.9 ms |
| L2 p50 / p95 | 3.3 / 8.0 ms | 3.5 / 8.8 ms |
| L3 p50 / p95 | — | 747 / 1704 ms |
| **End to end p50 / p95** | **31.6 / 59.1 ms** | **1252 / 2140 ms** |
| signature check | 107 µs/msg | 129 µs/msg |
| gateway CPU (mean) | 8 % | 16 % |
| backlog (mean / max) | 0.1 / 2 | 0.1 / 2 |
| on chain | — | 2.01 tx/s · 117.7 msgs anchored/s · 241/241 batches confirmed |
| lost / rejected | 0 / 0 | 0 / 0 |

The headline: anchoring costs essentially nothing on the ingest path — L1 and L2 are
unchanged, the backlog never builds, and the chain keeps up with the full message rate at
2 tx/s because only one root per batch is written. The whole cost is L3, waiting for a
block, and it is bounded by the block period rather than by the load.

## Contributions

| Package | Owner | Scope |
|---|---|---|
| A | Popescu Daniel | Simulated devices and Ed25519 signing, netem, docker-compose stack, aggregation and plots. Owns L1 and the KPI definitions. |
| B | Galea Andreea | The gateway: signature verification, Merkle tree and proofs, 500 ms batching, InfluxDB writes, Grafana dashboards. Owns L2, signature cost and backlog. |
| C | Bălan Ștefan | The Besu QBFT devnet and validators, `Anchor.sol` and its tests, the web3.py anchor client, the experiment harness and demo scripts; later full-record mode and the tamper test. Owns L3 and transactions/s. |

One change against the week-4 roadmap: the experiment harness (`run_experiments.py`) and the
demo scripts moved from A to C, because they drive the chain — `start.sh` waits for blocks
and deploys the contract, and `run_experiments.py` refuses to start without the validators
and a deployed `Anchor`. Reason recorded in `docs/Roadmap_midterm.md`.

## Declaration

<!-- Required by the project PDF: "Use of AI assistants (for code or text) is not allowed
     and must be declared in the report." Fill this in before the midterm; do not submit
     with the placeholder still here. -->

TODO — to be completed and signed by all three members.

## Repository layout

```
edge/        simulated sensors, their keys, the container that applies netem
gateway/     the gateway, the Merkle tree, the web3 anchor client
contracts/   Anchor.sol
test/        Hardhat tests for the contract
chain/       deployment script
besu/        QBFT genesis and the four validator keys
compose/     docker-compose, Mosquitto config, Grafana provisioning
analysis/    plot_latency.py — computes every KPI and draws every figure
results/     run logs (CSV, gzipped when committed) and plots
docs/        roadmap, study notes, figures
```
