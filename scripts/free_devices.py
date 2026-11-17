#!/usr/bin/env python3
"""List connected real devices and whether another session is operating them."""
import argparse
import fcntl
import hashlib
import json
import os
from pathlib import Path
import shutil
import signal
import subprocess
import sys
import tempfile
import time


def held_locks(device_ids):
    held = []
    for device_id in device_ids:
        key = hashlib.sha256(device_id.encode()).hexdigest()[:16]
        for prefix in ("mobile-runner-", "mobile-record-", "mobile-hold-"):
            path = Path(tempfile.gettempdir()) / (prefix + key + ".lock")
            if not path.exists():
                continue
            with open(path, "a") as handle:
                try:
                    fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
                    fcntl.flock(handle, fcntl.LOCK_UN)
                except BlockingIOError:
                    held.append(path.name)
    return held


def processes_naming(device_ids, process_table):
    own_pid = str(os.getpid())
    return [line.strip() for line in process_table
            if any(device_id in line for device_id in device_ids)
            and line.split(None, 1)[0] != own_pid]


def ios_devices():
    if not shutil.which("xcrun"):
        return []
    output = subprocess.run(["xcrun", "devicectl", "list", "devices", "-j", "/dev/stdout", "-q"],
                            capture_output=True, text=True, check=True).stdout
    devices = []
    for device in json.loads(output)["result"]["devices"]:
        hardware = device["hardwareProperties"]
        # A paired phone reachable by cable or network is usable; its tunnel opens on demand.
        if hardware.get("reality") != "physical" or not device["connectionProperties"].get("transportType"):
            continue
        devices.append({"platform": "ios", "name": device["deviceProperties"]["name"],
                        "os": device["deviceProperties"].get("osVersionNumber"),
                        "transport": device["connectionProperties"]["transportType"],
                        "ids": sorted({hardware["udid"], device["identifier"]})})
    return devices


def android_devices():
    adb = shutil.which("adb") or str(Path.home() / "Library/Android/sdk/platform-tools/adb")
    if not Path(adb).exists():
        return []
    lines = subprocess.run([adb, "devices", "-l"], capture_output=True, text=True, check=True).stdout.splitlines()[1:]
    devices = []
    for line in lines:
        fields = line.split()
        if len(fields) < 2 or fields[1] != "device" or fields[0].startswith("emulator-"):
            continue
        model = next((field.split(":", 1)[1] for field in fields if field.startswith("model:")), fields[0])
        devices.append({"platform": "android", "name": model, "ids": [fields[0]]})
    return devices


QUEUE_DIR = Path(tempfile.gettempdir()) / "mobile-device-queue"


def hold_lock_path(device_id):
    return Path(tempfile.gettempdir()) / ("mobile-hold-" + hashlib.sha256(device_id.encode()).hexdigest()[:16] + ".lock")


def wait_for_release(device_id):
    print(json.dumps({"holding": device_id}), flush=True)
    print("send q or Ctrl-C to release", flush=True)
    try:
        while input().strip() != "q":
            pass
    except (EOFError, KeyboardInterrupt):
        pass


def hold(device_id):
    with open(hold_lock_path(device_id), "w") as handle:
        fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
        wait_for_release(device_id)


def pid_alive(pid):
    try:
        os.kill(pid, 0)
        return True
    except ProcessLookupError:
        return False
    except PermissionError:
        return True


def acquire(platform, label, poll_seconds):
    """Wait in a FIFO queue shared by all sessions until a matching real device is free, then hold it."""
    QUEUE_DIR.mkdir(exist_ok=True)
    entry = QUEUE_DIR / ("%d-%d.json" % (time.time_ns(), os.getpid()))
    entry.write_text(json.dumps({"pid": os.getpid(), "platform": platform, "label": label}))
    announced = False
    try:
        while True:
            waiters = []
            for path in sorted(QUEUE_DIR.glob("*.json")):
                try:
                    waiter = json.loads(path.read_text())
                except (FileNotFoundError, json.JSONDecodeError):
                    continue
                if pid_alive(waiter["pid"]):
                    waiters.append((path, waiter))
                else:
                    path.unlink(missing_ok=True)
            report = scan()
            free = [device for device in report if device["free"]]
            for path, waiter in waiters:
                match = next((device for device in free
                              if waiter["platform"] in ("any", device["platform"])), None)
                if match is None:
                    continue
                free.remove(match)
                if path != entry:
                    continue
                handle = open(hold_lock_path(match["ids"][0]), "w")
                try:
                    fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
                except BlockingIOError:
                    handle.close()
                    break
                entry.unlink(missing_ok=True)
                print(json.dumps({"acquired": match}, ensure_ascii=False), flush=True)
                wait_for_release(match["ids"][0])
                handle.close()
                return
            if not announced:
                position = [path for path, _ in waiters].index(entry) + 1 if entry in [p for p, _ in waiters] else "?"
                print(json.dumps({"waiting": label, "position": position,
                                  "queue": [waiter["label"] for _, waiter in waiters]}, ensure_ascii=False), flush=True)
                announced = True
            time.sleep(poll_seconds)
    finally:
        entry.unlink(missing_ok=True)


def scan():
    process_table = subprocess.run(["ps", "-axo", "pid=,command="], capture_output=True, text=True,
                                   check=True).stdout.splitlines()
    report = []
    for device in ios_devices() + android_devices():
        locks = held_locks(device["ids"])
        processes = processes_naming(device["ids"], process_table)
        report.append({**device, "free": not locks and not processes,
                       "heldLocks": locks, "processes": processes})
    return report


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--hold", metavar="DEVICE_ID",
                        help="Mark a device as in use (for paths without launch_runner, e.g. adb) until q/Ctrl-C")
    parser.add_argument("--acquire", choices=["ios", "android", "any"],
                        help="Queue (FIFO across sessions) for a free real device of this platform, hold it until q/Ctrl-C")
    parser.add_argument("--label", default="unlabeled", help="Who is waiting, shown to other waiters (e.g. ticket key)")
    parser.add_argument("--poll", type=float, default=15.0)
    parser.add_argument("--queue", action="store_true", help="Show waiting sessions")
    args = parser.parse_args()
    if args.hold:
        hold(args.hold)
        return
    if args.acquire:
        signal.signal(signal.SIGTERM, lambda *_: sys.exit(0))
        acquire(args.acquire, args.label, args.poll)
        return
    if args.queue:
        QUEUE_DIR.mkdir(exist_ok=True)
        waiters = [json.loads(path.read_text()) for path in sorted(QUEUE_DIR.glob("*.json"))]
        print(json.dumps([waiter for waiter in waiters if pid_alive(waiter["pid"])], ensure_ascii=False, indent=2))
        return
    print(json.dumps(scan(), ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
