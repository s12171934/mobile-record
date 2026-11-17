#!/usr/bin/env python3
"""List connected real devices and whether another session is operating them."""
import argparse
import fcntl
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import tempfile


def held_locks(device_ids):
    held = []
    for device_id in device_ids:
        key = hashlib.sha256(device_id.encode()).hexdigest()[:16]
        for prefix in ("mobile-runner-", "mobile-record-"):
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
        if device["connectionProperties"].get("tunnelState") != "connected":
            continue
        udid = device["hardwareProperties"]["udid"]
        devices.append({"platform": "ios", "name": device["deviceProperties"]["name"],
                        "ids": sorted({udid, device["identifier"]})})
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


def hold(device_id):
    key = hashlib.sha256(device_id.encode()).hexdigest()[:16]
    with open(Path(tempfile.gettempdir()) / ("mobile-runner-" + key + ".lock"), "w") as handle:
        fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
        print("holding " + device_id + "; send q or Ctrl-C to release", flush=True)
        try:
            while input().strip() != "q":
                pass
        except (EOFError, KeyboardInterrupt):
            pass


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--hold", metavar="DEVICE_ID",
                        help="Mark a device as in use (for paths without launch_runner, e.g. adb) until q/Ctrl-C")
    args = parser.parse_args()
    if args.hold:
        hold(args.hold)
        return
    process_table = subprocess.run(["ps", "-axo", "pid=,command="], capture_output=True, text=True,
                                   check=True).stdout.splitlines()
    report = []
    for device in ios_devices() + android_devices():
        locks = held_locks(device["ids"])
        processes = processes_naming(device["ids"], process_table)
        report.append({**device, "free": not locks and not processes,
                       "heldLocks": locks, "processes": processes})
    print(json.dumps(report, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
