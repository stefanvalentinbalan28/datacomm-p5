# Project 5 — Blockchain-anchored IoT data over MQTT

**Updated roadmap, midterm (week 7–8).** Data Communications 2026–2027.
Team: Galea Andreea, Popescu Daniel, Bălan Ștefan. Variant: Hyperledger Besu with QBFT,
4 validators. Supersedes `Plan_Project5.docx` (week 4).

## 1. What we are building

Simulated IoT devices sign their own messages with Ed25519 and publish them over MQTT. A
gateway verifies every signature, rejects tampered and late messages, and stores the full
record in InfluxDB. In *anchoring* mode it additionally closes a batch every 500 ms, builds
a Merkle tree over the batch and writes only the root on a private Besu chain, storing each
message's proof next to the message. We measure what that integrity costs against the same
pipeline with no chain at all.

Everything runs on one machine inside WSL2, so every timestamp shares one clock. Network
impairment is `tc netem` 20 ms delay, 5 ms jitter, 1 % loss, on the device side only.

## 2. Who owns what

| Package | Owner | Scope | Owns |
|---|---|---|---|
| A | Popescu Daniel | Simulated devices and Ed25519 signing, netem, the docker-compose stack, aggregation, KPI computation and all plots | L1, the KPI definitions |
| B | Galea Andreea | The gateway: signature verification, Merkle tree and proofs, 500 ms batching, InfluxDB writes, Grafana dashboards | L2, signature cost, backlog |
| C | Bălan Ștefan | Besu QBFT devnet and validators, `Anchor.sol` and its tests, the web3.py anchor client, the experiment harness and demo scripts; later full-record mode and the tamper test | L3, transactions/s |

**Change against the week-4 roadmap.** The experiment harness (`run_experiments.py`) and the
demo scripts (`start.sh`, `startinf.sh`, `stop.sh`) move from package A to package C. Reason:
they are chain-driven, not sensor-driven — `start.sh` polls `eth_blockNumber` and deploys the
contract before it runs anything, and `run_experiments.py` refuses to start unless the four
validators are up and `chain/deployment.json` exists. Keeping them with the person who owns
the chain removes the only cross-package dependency in the run path, and it evens the three
packages out (≈710 / 590 / 590 lines instead of ≈960 / 590 / 330).

Each of us commits from our own GitHub account and presents at both presentations.

## 3. Where we are — midterm evidence

The midterm bar is *50 devices, no-chain vs anchoring, end-to-end latency (p50/p95) and
anchoring rate plotted, one confirmed anchor shown live*. All of it is met.

| Phase | State |
|---|---|
| P0 Foundations | done — Mosquitto, InfluxDB, Grafana, 4-validator QBFT devnet, `Anchor` deployed, signed publishers verified |
| P1 Baseline (no-chain) | done — 50 devices → gateway → InfluxDB, per-message log `t_pub`, `t_gw`, `t_db` |
| P2 Anchoring | done — 500 ms batches, Merkle root on chain, batch log `t_batch_close`, `t_tx_sent`, `t_confirmed` |

Measured run (seed 1, 50 devices, 120 s, first 20 s discarded; logs committed under
`results/experiments_20261105_051721/`):

| | no-chain | anchoring |
|---|---|---|
| throughput | 117.6 msg/s · 447 kbps | 117.6 msg/s · 447 kbps |
| end to end p50 / p95 | 31.6 / 59.1 ms | 1252 / 2140 ms |
| L3 p50 / p95 | — | 747 / 1704 ms |
| gateway backlog mean / max | 0.1 / 2 | 0.1 / 2 |
| on chain | — | 2.01 tx/s · 117.7 msgs anchored/s · 241/241 confirmed |

Reading: anchoring is free on the ingest path (L1 and L2 unchanged, backlog never builds,
2 tx/s carries the whole 118 msg/s because one root covers a whole batch). The entire cost
is L3 — waiting for a block — and it is bounded by the block period, not by the load.

## 4. What we cut for the midterm, and when it comes back

| Cut | Why | Returns |
|---|---|---|
| Full-record mode (one transaction per message) | It is a P3 item and it only becomes interesting at 200 devices, where it saturates the chain | weeks 9–10 |
| Tamper test | P3. `merkle.verify_proof` already exists and is unit-tested; what is missing is the InfluxDB-side test that modifies a stored record | weeks 9–10 |
| 200 devices | Needs 8–10 containers of 20–25 devices each, which is a separate piece of work from the measurement itself | weeks 11–12 |
| Seeds 2–5 | The midterm checkpoint asks for ≥1 seed. The harness already loops over seeds 1–5; it is a run-time cost, not a code cost | weeks 11–12 |
| Block period {1, 5} s and batch Δ {250, 1000} ms sweeps | One-factor-at-a-time points on top of the main matrix | weeks 11–12 |
| Grafana beyond the single live dashboard | `p5-live` is enough to show the system running; the analysis dashboards are report work | weeks 13–14 |
| Extensions (anomaly detection, signature schemes, validator stress) | Optional, and the PDF forbids starting one before the core matrix is complete | only if weeks 13–14 allow |

## 5. The experiment matrix we still owe

Unchanged from the week-4 plan — the midterm did not force us to shrink it:

- 3 modes {no-chain, anchoring, full-record} × 2 scales {50, 200 devices} × 5 repetitions ×
  120 s = **30 runs**
- anchoring at 200 devices, one factor at a time: block period {1, 5} s and batch Δ
  {250, 1000} ms → 4 extra points × 5 repetitions = **20 runs**
- **50 runs, ≈2 h of run time**, seeds 1–5 identical across modes, first 20 s of each run
  discarded.

One script runs the whole matrix and produces the plots: `python run_experiments.py`.

## 6. Schedule

| Weeks | What |
|---|---|
| 3 | P0: stack up, devnet running, one anchor confirmed from Python — done |
| 4 | Roadmap — done |
| 5–6 | P1 then P2, log format frozen — done |
| 7–8 | **Midterm**: 50 devices, no-chain vs anchoring, latency from the logs, one anchor confirmed live — this document |
| 9–10 | P3 full-record and the tamper test. Code freeze, pilot run on one seed |
| 11–12 | Run the matrix overnight; analysis and confidence intervals; first extension if there is room |
| 13–14 | Final presentation, report, README |

## 7. Risks and fallbacks

| Risk | State | Fallback |
|---|---|---|
| Besu with 4 validators eats the memory | tested — `-Xmx512m` per node, logging at WARN, stable over 2 h of runs | drop to a 1-validator devnet and say so |
| 200 containers do not fit on a laptop | not yet tested | 8–10 containers of 20–25 devices each; this is our only planned deviation from the scenario defaults |
| netem missing from the WSL2 kernel | tested in P0 — works, applied inside the sensor container | Ubuntu VM |
| Full-record saturates the chain at 200 devices | expected, and it is a result, not a failure | report the backlog curve over time |
| A single run exceeds 10 minutes | not observed; runs are 120 s | 3 seeds instead of 5, with the reason written down |
| L3 dominates end-to-end latency and hides everything else | observed | plots use a log y-axis and report L1, L2, L3 separately as well as end to end |
