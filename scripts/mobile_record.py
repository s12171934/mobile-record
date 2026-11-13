#!/usr/bin/env python3
"""Observe and operate an already running DeviceKit iOS runner. Stdlib + ffmpeg."""
import argparse
import base64
import datetime as dt
import fcntl
import hashlib
import ipaddress
import json
from pathlib import Path
import subprocess
import tempfile
import time
import urllib.parse
import urllib.request


def save(path, value):
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n")


def event(run, kind, **data):
    entry = {"at": time.time(), "kind": kind, **data}
    with (run / "events.jsonl").open("a") as stream:
        stream.write(json.dumps(entry, ensure_ascii=False) + "\n")


def request(endpoint, path, payload=None):
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    data = None if payload is None else json.dumps(payload).encode()
    req = urllib.request.Request(endpoint + path, data=data,
                                 headers={"Content-Type": "application/json"})
    return opener.open(req, timeout=45).read()


def rpc(config, method, params):
    response = json.loads(request(config["endpoint"], "/rpc", {
        "jsonrpc": "2.0", "id": 1, "method": method, "params": params}))
    if "error" in response:
        raise RuntimeError(response["error"])
    return response["result"]


def active_display(device):
    with tempfile.TemporaryDirectory() as temp:
        target = Path(temp) / "displays.json"
        subprocess.run(["xcrun", "devicectl", "device", "info", "displays",
                        "--device", device, "--json-output", str(target)],
                       check=True, stdout=subprocess.DEVNULL)
        result = json.loads(target.read_text())["result"]
    active = [d for d in result["displays"] if d.get("active")]
    if len(active) != 1:
        raise RuntimeError("Select a single active display before testing")
    d = active[0]
    return {k: d[k] for k in ("displayId", "uniqueId", "pointScale", "bounds", "currentOrientation")}


def walk(node):
    if isinstance(node, list):
        for child in node:
            yield from walk(child)
    elif isinstance(node, dict):
        yield node
        for child in node.get("children", []):
            yield from walk(child)


def snapshot(run, config, name, observation=None):
    # A screenshot and AX snapshot are sequential observations, not an atomic frame.
    display = observation["display"] if observation else active_display(config["device"])
    foreground = observation["foreground"] if observation else rpc(config, "device.apps.foreground", {"fields": []})
    screen = rpc(config, "device.screenshot", {"format": "png"})
    image = run / (name + ".png")
    image.write_bytes(base64.b64decode(screen["data"].split(",")[-1]))
    raw = observation["raw"] if observation else rpc(config, "device.dump.ui", {"format": "raw"})
    save(run / (name + ".ui.json"), raw)
    nodes = list(walk(raw))
    windows = [n for n in nodes if n.get("elementType") == 4
               and n.get("displayID") == display["displayId"]]
    if not windows:
        raise RuntimeError("No AX window matches the active display; inspect screenshot")
    frame = max(windows, key=lambda n: n["frame"]["Width"] * n["frame"]["Height"])["frame"]
    keyboard_visible = any(n.get("elementType") == 19 and n.get("displayID") == display["displayId"] for n in nodes)
    text_focused = any(n.get("elementType") in (45, 49, 50, 52) and n.get("hasFocus")
                       and n.get("displayID") == display["displayId"] for n in nodes)
    geometry = {"display": display, "window": frame, "foreground": foreground,
                "keyboardVisible": keyboard_visible, "textFocused": text_focused,
                "rawFile": name + ".ui.json",
                "capturedAt": time.time()}
    save(run / "geometry.json", geometry)
    compact = []
    for n in nodes:
        if n.get("elementType") not in (9, 20, 42, 45, 48, 49, 50, 52, 19):
            continue
        if not n.get("label") and n.get("elementType") != 52:
            continue
        f = n["frame"]
        compact.append({"type": n["elementType"], "label": n["label"][:160],
                        "value": n.get("value"), "displayID": n.get("displayID"),
                        "center": [f["X"] + f["Width"] / 2 - frame["X"],
                                   f["Y"] + f["Height"] / 2 - frame["Y"]]})
    save(run / (name + ".summary.json"), {**geometry, "nodes": compact})
    event(run, "snapshot", name=name, foreground=foreground)
    return {"image": str(image), "summary": str(run / (name + ".summary.json")), **geometry}


def checked_geometry(run, config):
    g = json.loads((run / "geometry.json").read_text())
    if time.time() - g["capturedAt"] > 90:
        raise RuntimeError("Snapshot is stale; capture again")
    if active_display(config["device"]) != g["display"]:
        raise RuntimeError("Display or orientation changed; capture again")
    if rpc(config, "device.apps.foreground", {"fields": []}) != g["foreground"]:
        raise RuntimeError("Foreground app changed; capture again")
    return g


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--run", type=Path, required=True)
    sub = parser.add_subparsers(dest="command", required=True)
    init = sub.add_parser("init")
    init.add_argument("--endpoint", required=True)
    init.add_argument("--device", required=True)
    init.add_argument("--display-tap-supported", action="store_true",
                      help="Only set after verifying the runner was built with the bundled patch")
    capture = sub.add_parser("snapshot")
    capture.add_argument("--name", required=True)
    url = sub.add_parser("url")
    url.add_argument("--url", required=True)
    tap = sub.add_parser("tap")
    tap.add_argument("--x", type=float, required=True)
    tap.add_argument("--y", type=float, required=True)
    key = sub.add_parser("key", help="Tap one visible software-keyboard key, then capture the result")
    key.add_argument("--label", required=True)
    note = sub.add_parser("note")
    note.add_argument("--say", required=True)
    record = sub.add_parser("record")
    record.add_argument("--rotation", choices=["none", "cw", "ccw", "half"], required=True)
    record.add_argument("--limit", type=int, default=180)
    sub.add_parser("finish")
    args = parser.parse_args()
    run = args.run.expanduser().resolve()
    run.mkdir(parents=True, exist_ok=True)
    if args.command == "init":
        endpoint = urllib.parse.urlsplit(args.endpoint)
        address = ipaddress.ip_address(endpoint.hostname)
        if (endpoint.scheme != "http" or not address.is_private or address.is_unspecified
                or endpoint.username or endpoint.password or endpoint.path not in ("", "/")
                or endpoint.query or endpoint.fragment):
            raise ValueError("Use an explicit paired-device/private IP HTTP endpoint")
        config = {"endpoint": args.endpoint.rstrip("/"), "device": args.device,
                  "displayTapSupported": args.display_tap_supported}
        if (run / "session.json").exists():
            raise RuntimeError("Session already exists; use a fresh directory")
        if request(config["endpoint"], "/health").decode().strip() != "OK":
            raise RuntimeError("Runner is not ready")
        save(run / "session.json", config)
        event(run, "init")
        print(json.dumps(active_display(config["device"]), ensure_ascii=False))
        return
    config = json.loads((run / "session.json").read_text())
    if args.command == "snapshot":
        if Path(args.name).name != args.name:
            raise ValueError("Snapshot name must be a filename stem")
        print(json.dumps(snapshot(run, config, args.name), ensure_ascii=False))
    elif args.command in ("tap", "key", "url"):
        if args.command in ("tap", "key"):
            if not config.get("displayTapSupported"):
                raise RuntimeError("Display tap patch not verified; refuse to probe unknown dryRun fields")
            g = checked_geometry(run, config)
            x, y = (args.x, args.y) if args.command == "tap" else (None, None)
            if args.command == "key":
                if not g.get("keyboardVisible") or not g.get("textFocused"):
                    raise RuntimeError("Capture a visible keyboard and focused text field first")
                raw = json.loads((run / g["rawFile"]).read_text())
                keyboards = [n for n in walk(raw) if n.get("elementType") == 19
                             and n.get("displayID") == g["display"]["displayId"]]
                centers = set()
                for keyboard in keyboards:
                    for n in walk(keyboard):
                        if n.get("label") == args.label and n.get("elementType") in (9, 20):
                            f = n["frame"]
                            if f["Width"] > 0 and f["Height"] > 0:
                                centers.add((f["X"] + f["Width"] / 2 - g["window"]["X"],
                                             f["Y"] + f["Height"] / 2 - g["window"]["Y"]))
                if len(centers) != 1:
                    raise RuntimeError(f"Expected one visible key for {args.label!r}, found {len(centers)}")
                x, y = centers.pop()
            if not (0 <= x < g["window"]["Width"] and 0 <= y < g["window"]["Height"]):
                raise ValueError("Tap is outside the observed viewport")
            params = {"x": x, "y": y, "displayID": g["display"]["displayId"]}
            probe = rpc(config, "device.io.tap", {**params, "dryRun": True})
            if (probe.get("executed") is not False or not probe.get("displayTargetingAvailable")
                    or probe.get("selectedDisplayID") != params["displayID"]):
                raise RuntimeError("Runner does not confirm display targeting")
        else:
            params = {"url": args.url}
        method = "device.url" if args.command == "url" else "device.io.tap"
        event(run, "action-start", method=method, params=params, keyLabel=getattr(args, "label", None))
        result = rpc(config, method, params)
        event(run, "action-result", method=method, result=result)
        print(json.dumps(result, ensure_ascii=False))
        if args.command == "key":
            print(json.dumps(snapshot(run, config, "key-" + str(time.time_ns())), ensure_ascii=False))
    elif args.command == "note":
        event(run, "note", text=args.say)
    elif args.command == "record":
        if not 1 <= args.limit <= 600:
            raise ValueError("Recording limit must be 1..600 seconds")
        # A separate observer owns the device recording lock; control calls remain available.
        key = hashlib.sha256(config["device"].encode()).hexdigest()[:16]
        lock = open(Path(tempfile.gettempdir()) / ("mobile-record-" + key + ".lock"), "w")
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        video = run / "video.mp4"
        if video.exists():
            raise RuntimeError("Video already exists; use a fresh run")
        rotation = {"none": "", "cw": "transpose=clock,", "ccw": "transpose=cclock,",
                    "half": "hflip,vflip,"}[args.rotation]
        started = time.time()
        save(run / "recording.json", {"startedAt": started, "rotation": args.rotation})
        event(run, "record-start")
        cmd = ["ffmpeg", "-hide_banner", "-loglevel", "error", "-use_wallclock_as_timestamps", "1",
               "-rw_timeout", "15000000", "-f", "mpjpeg", "-i",
               config["endpoint"] + "/mjpeg?fps=5&quality=65&scale=50", "-vf",
               rotation + "scale=trunc(iw/2)*2:trunc(ih/2)*2", "-fps_mode", "vfr",
               "-c:v", "h264_videotoolbox", "-b:v", "2M", "-movflags", "+faststart",
               "-t", str(args.limit), "-n", str(video)]
        save(run / "record-command.json", cmd)
        print("Recording; send q to finalize", flush=True)
        code = subprocess.call(cmd)
        event(run, "record-stop", exitCode=code)
        raise SystemExit(code)
    elif args.command == "finish":
        video = run / "video.mp4"
        # Encoding/decoding integrity is separate from the product scenario verdict.
        probe = subprocess.check_output(["ffprobe", "-v", "error", "-select_streams", "v:0",
                   "-show_entries", "stream=width,height,nb_frames:format=duration,size", "-of", "json", str(video)])
        metadata = json.loads(probe)
        duration = float(metadata["format"]["duration"])
        if duration <= 0 or not metadata["streams"]:
            raise RuntimeError("No playable video frames")
        subprocess.run(["ffmpeg", "-v", "error", "-i", str(video), "-f", "null", "-"], check=True)
        for name, seconds in [("head", 0), ("tail", max(0, duration - 1))]:
            subprocess.run(["ffmpeg", "-v", "error", "-ss", str(seconds), "-i", str(video),
                            "-frames:v", "1", "-y", str(run / (name + ".png"))], check=True)
        events = [json.loads(line) for line in (run / "events.jsonl").read_text().splitlines()]
        start = json.loads((run / "recording.json").read_text())["startedAt"]
        save(run / "video.actions.json", {"clock": "approximate wall time from recorder invocation",
             "recordingStartedAt": dt.datetime.fromtimestamp(start, dt.timezone.utc).isoformat(), "events": events})
        timeline = []
        for e in events:
            if e["at"] < start:
                continue
            seconds = max(0, int(e["at"] - start))
            title = e.get("text") or e.get("method") or e.get("name") or e["kind"]
            timeline.append(f"{seconds // 60:02}:{seconds % 60:02} {title}")
        (run / "video.timeline.txt").write_text("\n".join(timeline) + "\n")
        save(run / "video.metadata.json", metadata)
        print(json.dumps(metadata, ensure_ascii=False))


if __name__ == "__main__":
    main()
