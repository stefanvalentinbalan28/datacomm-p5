"""
Reads the gateway CSV logs and computes / plots the latency and anchoring KPIs.

    L1  = t_gw - t_pub              (device  -> gateway)
    L2  = t_db - t_gw               (gateway -> InfluxDB)
    L3  = t_confirmed - t_batch_close   (batch closed -> root confirmed on chain; anchoring only)
    E2E = end to end:
            no-chain:  t_db - t_pub          (message stored)
            anchoring: t_confirmed - t_pub   (message stored AND its batch root confirmed on chain)

Usage:
    python analysis/plot_latency.py results/nochain_20261105_101500.csv results/anchoring_20261105_103000.csv
    python analysis/plot_latency.py results/*.csv

The mode is taken from the start of the file name: nochain_..., anchoring_..., fullrecord_...
Several files of the same mode = several repetitions (seeds) -> mean and 95% confidence interval.
For an anchoring run, the batch log (<same name>_batches.csv) is read automatically.
"""
import os
import sys
import numpy as np
import pandas as pd
import matplotlib
matplotlib.use("Agg")  # draw straight into an image file, no window needed
import matplotlib.pyplot as plt
import matplotlib.ticker

WARMUP_SECONDS = 20                 # the first 20 s of every run are ignored
OUTPUT_NAME = "latency.png"         # saved in the same folder as the (first) log file
L3_OUTPUT_NAME = "l3_distribution.png"
BLOCK_PERIOD_SECONDS = 2            # QBFT block period, see besu/qbftConfigFile.json

SEGMENTS = ["L1", "L2", "L3", "E2E"]
SEGMENT_NAMES = {
    "L1": "L1\ndevice → gateway",
    "L2": "L2\ngateway → InfluxDB",
    "L3": "L3\nbatch → on chain",
    "E2E": "End to end",
}

# Every mode always gets the same color
MODE_ORDER = ["nochain", "anchoring", "fullrecord"]
MODE_COLORS = {
    "nochain": "#2a78d6",     # blue
    "anchoring": "#eb6834",   # orange
    "fullrecord": "#1baf7a",  # green
}

# Student t value for a 95% confidence interval, depending on (number of runs - 1)
T_95 = {1: 12.71, 2: 4.30, 3: 3.18, 4: 2.78, 5: 2.57, 6: 2.45, 7: 2.36, 8: 2.31, 9: 2.26}

TEXT_COLOR = "#3d3d3a"
GRID_COLOR = "#e4e3dd"


# ---------- Reading and computing ----------

def load_batches(path):
    """The batch log that belongs to a message log, only the confirmed batches. None if there is none."""
    batches_path = path.replace(".csv", "_batches.csv")
    if not os.path.exists(batches_path):
        return None
    batches = pd.read_csv(batches_path)
    batches = batches[batches["status"] == "confirmed"]
    if len(batches) == 0:
        return None
    return batches


def load_run(path):
    """Read one run, drop the warm-up and add the latency columns (in ms)."""
    data = pd.read_csv(path)

    # Keep only the messages that arrived after the warm-up
    start_time = data["t_gw"].min()
    data = data[data["t_gw"] >= start_time + WARMUP_SECONDS].copy()

    # Latencies in milliseconds (the times in the file are in seconds)
    data["L1"] = (data["t_gw"] - data["t_pub"]) * 1000
    data["L2"] = (data["t_db"] - data["t_gw"]) * 1000

    batches = load_batches(path)
    if batches is None:
        # no blockchain: the journey ends when the message is stored
        data["L3"] = np.nan
        data["E2E"] = (data["t_db"] - data["t_pub"]) * 1000
    else:
        # anchoring: add to every message the close and confirmation time of its batch
        batch_times = batches[["batch_id", "t_batch_close", "t_confirmed"]]
        data = data.merge(batch_times, on="batch_id", how="left")
        data["L3"] = (data["t_confirmed"] - data["t_batch_close"]) * 1000
        data["E2E"] = (data["t_confirmed"] - data["t_pub"]) * 1000

        # keep only the batches of the measured period (after warm-up)
        batches = batches[batches["t_batch_close"] >= start_time + WARMUP_SECONDS]

    return data, batches


def count_lost(data):
    """Count missing messages by looking for gaps in each sensor's seq numbers."""
    lost = 0
    for device in data["device_id"].unique():
        seq_numbers = data[data["device_id"] == device]["seq"]
        expected = seq_numbers.max() - seq_numbers.min() + 1
        received = seq_numbers.nunique()
        lost = lost + (expected - received)
    return lost


def latency_numbers(values, count_for_p99):
    """Mean, p50, p95 and p99 of a list of values (all None if there are no values)."""
    values = values.dropna()
    if len(values) == 0:
        return None, None, None, None
    mean = values.mean()
    p50 = np.percentile(values, 50)
    p95 = np.percentile(values, 95)
    p99 = None
    if count_for_p99 >= 1000:   # p99 only with at least 1000 samples (rule from the project PDF)
        p99 = np.percentile(values, 99)
    return mean, p50, p95, p99


def compute_stats(data, batches):
    """All the numbers we report for one run."""
    ok = data[data["status"] == "ok"]
    duration = data["t_gw"].max() - data["t_gw"].min()

    stats = {}
    stats["duration"] = duration
    stats["samples"] = len(ok)
    stats["throughput"] = len(ok) / duration
    stats["rejected"] = len(data) - len(ok)
    stats["lost"] = count_lost(data)
    stats["verify_us"] = ok["verify_us"].mean()

    # Offered load in kbps: all bytes the sensors sent, per second (sanity number from the PDF)
    stats["load_kbps"] = None
    if "msg_bytes" in data.columns:
        stats["load_kbps"] = data["msg_bytes"].sum() * 8 / duration / 1000

    for segment in SEGMENTS:
        if segment == "L3" and batches is not None:
            # L3 belongs to a batch, not to a message: one value per batch
            values = (batches["t_confirmed"] - batches["t_batch_close"]) * 1000
            mean, p50, p95, p99 = latency_numbers(values, len(values))
        else:
            mean, p50, p95, p99 = latency_numbers(ok[segment], len(ok))
        stats[segment + "_mean"] = mean
        stats[segment + "_p50"] = p50
        stats[segment + "_p95"] = p95
        stats[segment + "_p99"] = p99

    # On-chain rate (anchoring only)
    stats["tx_per_s"] = None
    stats["anchored_per_s"] = None
    if batches is not None and len(batches) > 1:
        chain_duration = batches["t_confirmed"].max() - batches["t_confirmed"].min()
        stats["tx_per_s"] = len(batches) / chain_duration
        stats["anchored_per_s"] = batches["count"].sum() / chain_duration
    return stats


def number_text(value, decimals=2):
    if value is None:
        return "n/a"
    return f"{value:.{decimals}f}"


def print_stats(path, mode, stats):
    print()
    print(f"{os.path.basename(path)}  ({mode})")
    print(f"  measured {stats['duration']:.0f} s after {WARMUP_SECONDS} s warm-up")
    print(f"  {stats['samples']} messages, {stats['throughput']:.1f} msg/s, "
          f"{number_text(stats['load_kbps'], 0)} kbps, {stats['rejected']} rejected, {stats['lost']} lost")
    print(f"  signature check: {stats['verify_us']:.1f} µs per message (mean)")
    for segment in SEGMENTS:
        print(f"  {segment:4s}  mean {number_text(stats[segment + '_mean']):>8}   "
              f"p50 {number_text(stats[segment + '_p50']):>8}   "
              f"p95 {number_text(stats[segment + '_p95']):>8}   "
              f"p99 {number_text(stats[segment + '_p99']):>8} ms")
    if stats["tx_per_s"] is not None:
        print(f"  on chain: {stats['tx_per_s']:.2f} tx/s, {stats['anchored_per_s']:.1f} messages anchored/s")


def mean_and_ci(values):
    """Mean of the values and the half-width of their 95% confidence interval."""
    n = len(values)
    mean = np.mean(values)
    if n < 2:
        return mean, 0  # with a single run no interval can be computed
    standard_deviation = np.std(values, ddof=1)
    t_value = T_95.get(n - 1, 1.96)
    half_width = t_value * standard_deviation / np.sqrt(n)
    return mean, half_width


def get_mode(path):
    """'results/nochain_20261105_101500.csv' -> 'nochain'"""
    filename = os.path.basename(path)
    return filename.split("_")[0]


# ---------- Drawing ----------

def style_axis(ax):
    """Light grid, no top/right border, grey text."""
    ax.spines["top"].set_visible(False)
    ax.spines["right"].set_visible(False)
    ax.spines["left"].set_color(GRID_COLOR)
    ax.spines["bottom"].set_color(GRID_COLOR)
    ax.tick_params(colors=TEXT_COLOR, labelsize=9)
    ax.yaxis.grid(True, color=GRID_COLOR, linewidth=0.8)
    ax.set_axisbelow(True)


def draw_bars(ax, percentile, modes, stats_by_mode):
    """One group of bars per segment (L1, L2, L3, E2E), one bar per mode.
    The y axis is logarithmic: the values go from ~2 ms to ~2000 ms."""
    x_positions = np.arange(len(SEGMENTS))   # 0, 1, 2, 3
    bar_width = 0.8 / len(modes)

    for i in range(len(modes)):
        mode = modes[i]
        runs = stats_by_mode[mode]

        heights = []
        errors = []
        for segment in SEGMENTS:
            values = []
            for stats in runs:
                if stats[segment + "_" + percentile] is not None:
                    values.append(stats[segment + "_" + percentile])
            if len(values) == 0:
                heights.append(np.nan)   # e.g. L3 in no-chain mode: no bar
                errors.append(0)
            else:
                mean, half_width = mean_and_ci(values)
                heights.append(mean)
                errors.append(half_width)

        # Shift the bars of each mode a bit to the side, so they stand next to each other
        positions = x_positions - 0.4 + bar_width * (i + 0.5)

        if len(runs) > 1:
            ax.bar(positions, heights, width=bar_width * 0.95, color=MODE_COLORS[mode], label=mode,
                   yerr=errors, capsize=4, ecolor=TEXT_COLOR)
        else:
            ax.bar(positions, heights, width=bar_width * 0.95, color=MODE_COLORS[mode], label=mode)

        # The value written above each bar
        for j in range(len(SEGMENTS)):
            if not np.isnan(heights[j]):
                ax.text(positions[j], heights[j] * 1.08, f"{heights[j]:.0f}" if heights[j] >= 10 else f"{heights[j]:.1f}",
                        ha="center", va="bottom", fontsize=8, color=TEXT_COLOR)

    labels = []
    for segment in SEGMENTS:
        labels.append(SEGMENT_NAMES[segment])
    ax.set_xticks(x_positions, labels)
    ax.set_yscale("log")
    ax.yaxis.set_major_formatter(matplotlib.ticker.ScalarFormatter())  # 10, 100 instead of 10^1, 10^2
    ax.set_ylabel("latency (ms, log scale)", color=TEXT_COLOR)
    ax.set_title(f"Latency {percentile}", color=TEXT_COLOR, fontsize=11, loc="left")
    style_axis(ax)


def draw_cdf(ax, modes, data_by_mode):
    """For every latency value: what fraction of messages were at least that fast."""
    for mode in modes:
        all_runs = pd.concat(data_by_mode[mode])                # all repetitions of this mode together
        latencies = np.sort(all_runs["E2E"].dropna().to_numpy())  # smallest to largest
        count = len(latencies)
        fraction = np.arange(1, count + 1) / count              # 1/count, 2/count, ..., 1
        ax.plot(latencies, fraction, color=MODE_COLORS[mode], linewidth=2, label=mode)

    ax.set_xscale("log")
    ax.xaxis.set_major_formatter(matplotlib.ticker.ScalarFormatter())
    ax.set_xlabel("end-to-end latency (ms, log scale)", color=TEXT_COLOR)
    ax.set_ylabel("fraction of messages", color=TEXT_COLOR)
    ax.set_title("End-to-end latency CDF", color=TEXT_COLOR, fontsize=11, loc="left")
    style_axis(ax)
    ax.xaxis.grid(True, color=GRID_COLOR, linewidth=0.8)


def draw_anchoring_rate(ax, data_by_mode, batches_by_mode, stats_by_mode):
    """First anchoring run: total messages stored and total messages anchored, over time.
    The slope of each line is the rate (messages per second); the horizontal distance
    between the lines is how far anchoring lags behind storing.
    (Counting per time window instead would jump up and down, because a block every 2 s
    sometimes lands just before and sometimes just after a window border.)"""
    ax.set_title("Anchoring rate (first anchoring run)", color=TEXT_COLOR, fontsize=11, loc="left")
    style_axis(ax)
    if "anchoring" not in batches_by_mode or batches_by_mode["anchoring"][0] is None:
        ax.text(0.5, 0.5, "no anchoring run yet", ha="center", va="center",
                transform=ax.transAxes, color=TEXT_COLOR)
        return

    data = data_by_mode["anchoring"][0]
    batches = batches_by_mode["anchoring"][0]
    stats = stats_by_mode["anchoring"][0]
    start = data["t_db"].min()

    # Stored: one step up for every message, at the moment InfluxDB confirmed it
    stored_times = np.sort(data["t_db"].to_numpy()) - start
    stored_total = np.arange(1, len(stored_times) + 1)

    # Anchored: one step up of "count" messages for every batch, at the moment it was confirmed
    batches = batches.sort_values("t_confirmed")
    anchored_times = batches["t_confirmed"].to_numpy() - start
    anchored_total = np.cumsum(batches["count"].to_numpy())

    ax.plot(stored_times, stored_total, color=MODE_COLORS["nochain"], linewidth=2,
            label=f"stored in InfluxDB ({stats['throughput']:.0f} msg/s)")
    ax.step(anchored_times, anchored_total, where="post", color=MODE_COLORS["anchoring"], linewidth=2,
            label=f"anchored on chain ({stats['anchored_per_s']:.0f} msg/s, {stats['tx_per_s']:.1f} tx/s)")
    ax.set_xlabel("time since end of warm-up (s)", color=TEXT_COLOR)
    ax.set_ylabel("messages so far", color=TEXT_COLOR)
    ax.set_ylim(bottom=0)
    ax.legend(frameon=False, fontsize=9, loc="upper left")


def add_sanity_numbers(figure, modes, stats_by_mode):
    """Runs, load, duration and sample count under the figure (required next to every result)."""
    lines = []
    for mode in modes:
        runs = stats_by_mode[mode]
        total_samples = 0
        throughputs = []
        durations = []
        for stats in runs:
            total_samples = total_samples + stats["samples"]
            throughputs.append(stats["throughput"])
            durations.append(stats["duration"])
        load = ""
        if runs[0]["load_kbps"] is not None:
            load = f" ({runs[0]['load_kbps']:.0f} kbps)"
        line = (f"{mode}: {len(runs)} run(s), {np.mean(throughputs):.0f} msg/s{load}, "
                f"{np.mean(durations):.0f} s measured per run, {total_samples} samples")
        if runs[0]["tx_per_s"] is not None:
            line = line + f", {runs[0]['tx_per_s']:.1f} tx/s on chain"
        lines.append(line)
    figure.text(0.01, 0.01, "   |   ".join(lines), fontsize=8, color=TEXT_COLOR)


# ---------- Main program ----------

def main():
    files = sys.argv[1:]
    if len(files) == 0:
        print("Usage: python analysis/plot_latency.py results/<file>.csv [more files...]")
        return

    stats_by_mode = {}     # mode -> list with the stats of each run
    data_by_mode = {}      # mode -> list with the "ok" messages of each run
    batches_by_mode = {}   # mode -> list with the confirmed batches of each run (or None)

    for path in sorted(files):
        # The gateway also writes *_batches.csv and *_gateway.csv; they are not message logs
        if path.endswith("_batches.csv") or path.endswith("_gateway.csv"):
            continue

        mode = get_mode(path)
        if mode not in MODE_COLORS:
            print(f"Skipping {path}: unknown mode '{mode}'")
            continue

        data, batches = load_run(path)
        stats = compute_stats(data, batches)
        print_stats(path, mode, stats)

        if mode not in stats_by_mode:
            stats_by_mode[mode] = []
            data_by_mode[mode] = []
            batches_by_mode[mode] = []
        stats_by_mode[mode].append(stats)
        data_by_mode[mode].append(data[data["status"] == "ok"])
        batches_by_mode[mode].append(batches)

    # Modes in a fixed order, so the colors and bar positions never change
    modes = []
    for mode in MODE_ORDER:
        if mode in stats_by_mode:
            modes.append(mode)

    figure, axes = plt.subplots(2, 2, figsize=(14, 9))
    draw_bars(axes[0][0], "p50", modes, stats_by_mode)
    draw_bars(axes[0][1], "p95", modes, stats_by_mode)
    draw_cdf(axes[1][0], modes, data_by_mode)
    draw_anchoring_rate(axes[1][1], data_by_mode, batches_by_mode, stats_by_mode)
    if len(modes) > 1:
        axes[0][0].legend(frameon=False, fontsize=9)
        axes[1][0].legend(frameon=False, fontsize=9)
    add_sanity_numbers(figure, modes, stats_by_mode)

    figure.tight_layout(rect=(0, 0.03, 1, 1))
    output_file = os.path.join(os.path.dirname(files[0]), OUTPUT_NAME)
    figure.savefig(output_file, dpi=150)
    print()
    print(f"Plot saved to {output_file}")
    save_l3_distribution(batches_by_mode, os.path.dirname(files[0]))


def save_l3_distribution(batches_by_mode, output_dir):
    """Second figure: how L3 (batch closed -> root confirmed) is spread over the batches.

    This is the figure that answers the pitfall from the project PDF: confirmation happens
    per block, so a batch that closes just after a block was sealed waits almost a whole
    block period, and one that closes just before waits almost nothing. The values should
    therefore fill the range 0..BLOCK_PERIOD_SECONDS fairly evenly, with a mean near half
    of it. Nothing here is typed by hand: every number comes from the batch log."""
    if "anchoring" not in batches_by_mode:
        return
    runs = []
    for batches in batches_by_mode["anchoring"]:
        if batches is not None:
            runs.append(batches)
    if len(runs) == 0:
        return

    all_batches = pd.concat(runs)
    l3 = ((all_batches["t_confirmed"] - all_batches["t_batch_close"]) * 1000).dropna()
    if len(l3) == 0:
        return

    figure, ax = plt.subplots(figsize=(9, 5))
    ax.hist(l3, bins=40, color=MODE_COLORS["anchoring"])
    for label, value in [("mean", l3.mean()), ("p50", np.percentile(l3, 50)),
                         ("p95", np.percentile(l3, 95))]:
        ax.axvline(value, color=TEXT_COLOR, linewidth=1, linestyle="--")
        ax.text(value, ax.get_ylim()[1] * 0.97, f" {label} {value:.0f} ms",
                fontsize=8, color=TEXT_COLOR, va="top")
    # The block period is the ceiling the waiting time cannot exceed by much
    ax.axvline(BLOCK_PERIOD_SECONDS * 1000, color=MODE_COLORS["nochain"], linewidth=1.5)
    ax.text(BLOCK_PERIOD_SECONDS * 1000, ax.get_ylim()[1] * 0.80,
            f" block period {BLOCK_PERIOD_SECONDS:.0f} s", fontsize=8, color=MODE_COLORS["nochain"], va="top")

    ax.set_xlabel("L3: batch closed -> root confirmed on chain (ms)", color=TEXT_COLOR)
    ax.set_ylabel("number of batches", color=TEXT_COLOR)
    ax.set_title(f"L3 distribution over {len(l3)} batches ({len(runs)} anchoring run(s))",
                 color=TEXT_COLOR, fontsize=11, loc="left")
    style_axis(ax)

    output_file = os.path.join(output_dir, L3_OUTPUT_NAME)
    figure.tight_layout()
    figure.savefig(output_file, dpi=150)
    print(f"Plot saved to {output_file}")


main()
