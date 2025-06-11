import os
import psutil
import time
import threading
import platform
from datetime import datetime
import traceback
import csv
from collections import defaultdict
import sys
import subprocess
import re
import shutil

WEIGHTS = {
    "cpu": 0.1,
    "core_imbalance": 0.1,
    "freq_ratio": 0.1,
    "python_cpu": 0.2,
    "python_ram": 0.2,
    "gpu_util": 0.2,
    "gpu_mem": 0.2,
    "gpu_temp": 0.1
}

def get_cpu_usage():
    return psutil.cpu_percent(interval=1)

def get_core_imbalance():
    per_core = psutil.cpu_percent(interval=1, percpu=True)
    return max(per_core) - min(per_core) if per_core else 0.0

def get_freq_ratio():
    try:
        freq = psutil.cpu_freq()
        return round(freq.current / freq.max, 2) if freq and freq.max else 0
    except:
        return 0

def get_all_python_processes():
    return [p for p in psutil.process_iter(['pid', 'name', 'cpu_percent', 'memory_info']) if 'python' in (p.info['name'] or '').lower()]

def get_gpu_metrics():
    possible_paths = ["nvidia-smi", "/usr/bin/nvidia-smi", "/usr/local/bin/nvidia-smi"]
    nvidia_smi_path = None

    for path in possible_paths:
        if shutil.which(path):
            nvidia_smi_path = shutil.which(path)
            break

    if not nvidia_smi_path:
        print("[GPU METRIC ERROR] 'nvidia-smi' not found in PATH or common locations.")
        return 0.0, 0.0, 0.0

    try:
        result = subprocess.check_output(
            [nvidia_smi_path, "--query-gpu=utilization.gpu,memory.used,memory.total,temperature.gpu",
             "--format=csv,noheader,nounits"],
            encoding='utf-8'
        ).strip()

        lines = result.splitlines()
        if not lines:
            return 0.0, 0.0, 0.0

        total_util = 0.0
        total_mem = 0.0
        total_temp = 0.0

        for line in lines:
            parts = re.split(r',\s*', line)
            if len(parts) != 4:
                continue

            util = float(parts[0])
            mem_used = float(parts[1])
            mem_total = float(parts[2])
            temp = float(parts[3])

            total_util += util
            total_mem += (mem_used / mem_total) * 100
            total_temp += temp

        gpu_count = len(lines)
        return (
            total_util / gpu_count,
            total_mem / gpu_count,
            total_temp / gpu_count
        )

    except Exception as e:
        print(f"[GPU METRIC ERROR] {e}")
        return 0.0, 0.0, 0.0

def log_metrics(monitored_pids, team_name, csv_path):
    total_ram = psutil.virtual_memory().total / (1024 ** 2)  # MB
    core_count = psutil.cpu_count(logical=True)
    stats = defaultdict(list)
    start_time = time.time()

    while monitored_pids:
        try:
            cpu = get_cpu_usage()
            core_imbalance = get_core_imbalance()
            freq_ratio = get_freq_ratio()

            total_proc_cpu = 0.0
            total_proc_mem = 0.0

            for pid in list(monitored_pids):
                try:
                    p = psutil.Process(pid)
                    total_proc_cpu += p.cpu_percent(interval=0.1)
                    total_proc_mem += p.memory_info().rss / (1024 ** 2)
                except (psutil.NoSuchProcess, psutil.AccessDenied):
                    monitored_pids.discard(pid)

            norm_cpu = (total_proc_cpu / (core_count * 100)) * 100
            norm_ram = (total_proc_mem / total_ram) * 100
            gpu_util, gpu_mem, gpu_temp = get_gpu_metrics()

            stats["cpu"].append(cpu)
            stats["core_imbalance"].append(core_imbalance)
            stats["freq_ratio"].append(freq_ratio)
            stats["python_cpu"].append(norm_cpu)
            stats["python_ram"].append(norm_ram)
            stats["gpu_util"].append(gpu_util)
            stats["gpu_mem"].append(gpu_mem)
            stats["gpu_temp"].append(gpu_temp)

            time.sleep(1)

        except Exception as e:
            print("[Error]", e)
            traceback.print_exc()
            break

    duration = time.time() - start_time

    if duration < 60:
        print(f"\nScripts ran for less than 60 seconds ({int(duration)}s). Skipping CSV generation.")
        return

    def avg(key): return sum(stats[key]) / len(stats[key]) if stats[key] else 0
    averages = {key: round(avg(key), 2) for key in WEIGHTS}
    final_score = round(sum(WEIGHTS[k] * averages[k] for k in WEIGHTS), 2)
    averages["final_score"] = final_score

    print(f"\nFinal Score for {team_name}: {final_score:.2f}")

    with open(csv_path, 'w', newline='') as f:
        writer = csv.DictWriter(f, fieldnames=list(averages.keys()))
        writer.writeheader()
        writer.writerow(averages)

    print(f"\nResults saved to: {os.path.abspath(csv_path)}")

def detect_and_monitor(team_name):
    start_time = time.time()
    initial_pids = {p.pid for p in get_all_python_processes()}
    monitored_pids = set()
    running = False
    t = None

    csv_path = os.path.join(os.getcwd(), f"{team_name}_benchmark.csv")
    print(f"\n🔍 Waiting for new Python scripts to launch for team: {team_name}...")

    try:
        while True:
            current_procs = get_all_python_processes()
            new_procs = [p for p in current_procs if p.pid not in initial_pids | monitored_pids]

            valid_new_pids = set()
            for p in new_procs:
                try:
                    proc = psutil.Process(p.pid)
                    if proc.create_time() > start_time:
                        if not any(ignore in proc.name().lower() for ignore in ['jupyter', 'pydev', 'spyder', 'conda']):
                            valid_new_pids.add(p.pid)
                except (psutil.NoSuchProcess, psutil.AccessDenied):
                    continue

            if valid_new_pids:
                monitored_pids.update(valid_new_pids)
                print(f"\nNew Python script(s) detected: {valid_new_pids}")
                if not running:
                    running = True
                    print("Starting monitoring...\n(Will stop when all new Python processes exit)")
                    t = threading.Thread(target=log_metrics, args=(monitored_pids, team_name, csv_path))
                    t.start()

            if running:
                alive_pids = [pid for pid in monitored_pids if psutil.pid_exists(pid)]
                if not alive_pids:
                    print("\nAll monitored Python processes have exited. Stopping monitoring...")
                    running = False
                    t.join()
                    break

            time.sleep(1)

    except KeyboardInterrupt:
        print("\nManual interrupt. Saving benchmark results...")
        if running and t:
            t.join()

if __name__ == "__main__":
    team_name = input("Enter your team name: ").strip().lower()
    if not team_name:
        print("Team name cannot be empty.")
        sys.exit(1)
    detect_and_monitor(team_name)
