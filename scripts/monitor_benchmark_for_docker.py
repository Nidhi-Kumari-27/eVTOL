import os
import psutil
import time
import threading
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
    "docker_cpu": 0.2,
    "docker_mem": 0.2,
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

def get_docker_metrics(container_name):
    if not container_name:
        return 0.0, 0.0

    try:
        cmd = [
            "docker", "stats", container_name,
            "--no-stream", "--format", "{{.CPUPerc}},{{.MemUsage}}"
        ]
        output = subprocess.check_output(cmd, encoding="utf-8").strip()
        cpu_str, mem_str = output.split(',')

        cpu = float(cpu_str.strip('%'))
        mem_used, _ = mem_str.strip().split('/')
        mem_mb = float(re.sub(r'[^\d.]', '', mem_used))  # crude parsing

        return round(cpu, 2), round(mem_mb, 2)

    except Exception as e:
        print(f"[DOCKER METRIC ERROR] {e}")
        return 0.0, 0.0

def get_gpu_metrics():
    possible_paths = ["nvidia-smi", "/usr/bin/nvidia-smi", "/usr/local/bin/nvidia-smi"]
    nvidia_smi_path = next((shutil.which(p) for p in possible_paths if shutil.which(p)), None)

    if not nvidia_smi_path:
        print("[GPU METRIC ERROR] 'nvidia-smi' not found.")
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

        total_util = total_mem = total_temp = 0.0
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

def log_metrics(team_name, csv_path, container_name):
    total_ram = psutil.virtual_memory().total / (1024 ** 2)  # MB
    stats = defaultdict(list)
    start_time = time.time()

    try:
        while True:
            cpu = get_cpu_usage()
            core_imbalance = get_core_imbalance()
            freq_ratio = get_freq_ratio()
            docker_cpu, docker_mem = get_docker_metrics(container_name)
            norm_docker_mem = (docker_mem / total_ram) * 100 if total_ram else 0.0
            gpu_util, gpu_mem, gpu_temp = get_gpu_metrics()

            stats["cpu"].append(cpu)
            stats["core_imbalance"].append(core_imbalance)
            stats["freq_ratio"].append(freq_ratio)
            stats["docker_cpu"].append(docker_cpu)
            stats["docker_mem"].append(norm_docker_mem)
            stats["gpu_util"].append(gpu_util)
            stats["gpu_mem"].append(gpu_mem)
            stats["gpu_temp"].append(gpu_temp)

            time.sleep(1)

    except KeyboardInterrupt:
        print("\nMonitoring interrupted manually.")

    except Exception as e:
        print("[Error]", e)
        traceback.print_exc()

    duration = time.time() - start_time

    if duration < 60:
        print(f"\nMonitoring ran for less than 60 seconds ({int(duration)}s). Skipping CSV generation.")
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

def monitor_only(team_name, container_name):
    csv_path = os.path.join(os.getcwd(), f"{team_name}_benchmark.csv")
    print(f"\n🔍 Monitoring system, Docker container '{container_name}', and GPU for team: {team_name}")
    print("Press Ctrl+C to stop monitoring...\n")
    log_metrics(team_name, csv_path, container_name)

if __name__ == "__main__":
    team_name = input("Enter your team name: ").strip().lower()
    if not team_name:
        print("Team name cannot be empty.")
        sys.exit(1)

    container_name = input("Enter Docker container name: ").strip()
    if not container_name:
        print("Docker container name cannot be empty.")
        sys.exit(1)

    monitor_only(team_name, container_name)
