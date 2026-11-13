#!/usr/bin/env python3
"""Launch an existing signed DeviceKit XCTest build over the current paired tunnel."""
import argparse
import fcntl
import hashlib
import json
import os
from pathlib import Path
import subprocess
import tempfile
import urllib.request


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--device", required=True)
    p.add_argument("--xctestrun", type=Path, required=True)
    p.add_argument("--out", type=Path, required=True)
    p.add_argument("--atest", required=True, help="Installed agent-dashboard bin/atest CLI")
    a = p.parse_args()
    if not a.xctestrun.is_file():
        p.error("Existing build-for-testing .xctestrun file is required")
    a.out.mkdir(parents=True, exist_ok=True)
    key = hashlib.sha256(a.device.encode()).hexdigest()[:16]
    lock = open(Path(tempfile.gettempdir()) / ("mobile-runner-" + key + ".lock"), "w")
    fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
    with tempfile.TemporaryDirectory() as temp:
        target = Path(temp) / "device.json"
        subprocess.run(["xcrun", "devicectl", "device", "info", "details", "--device", a.device,
                        "--json-output", str(target)], check=True, stdout=subprocess.DEVNULL)
        device = json.loads(target.read_text())["result"]
    connection = device["connectionProperties"]
    tunnel = connection["tunnelIPAddress"]
    endpoint = "http://[" + tunnel + "]:12004"
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    try:
        opener.open(endpoint + "/health", timeout=2).close()
    except OSError:
        pass
    else:
        raise RuntimeError("An existing runner answers here; attach or contact its owning session")
    (a.out / "endpoint.txt").write_text(endpoint)
    (a.out / "connection.json").write_text(json.dumps(connection, indent=2))
    print("Endpoint: " + endpoint, flush=True)
    env = dict(os.environ, TEST_RUNNER_DEVICEKIT_LISTEN_HOST="127.0.0.1," + tunnel)
    command = [a.atest, "run", "--kind", "xctest", "--label", "mobile-record DeviceKit runner", "--",
               "xcodebuild", "test-without-building", "-xctestrun", str(a.xctestrun.resolve()),
               "-destination", "id=" + a.device, "-parallel-testing-enabled", "NO",
               "-collect-test-diagnostics", "never"]
    return subprocess.call(command, env=env)


if __name__ == "__main__":
    raise SystemExit(main())
