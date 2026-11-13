#!/usr/bin/env python3
"""Run declarative iOS UI scenarios using native taps, condition waits and paced key taps."""
import argparse
import fcntl
import hashlib
import json
import math
from pathlib import Path
import tempfile
import time

import mobile_record as mobile


def center(node, window):
    f = node["frame"]
    return (f["X"] + f["Width"] / 2 - window["X"],
            f["Y"] + f["Height"] / 2 - window["Y"])


class ScenarioRunner:
    def __init__(self, run, config, spec, speed=1, tap_interval=.2,
                 key_interval=.12, poll_interval=.25, timeout=15, key_checkpoint="batch"):
        self.run, self.config, self.spec = run, config, spec
        self.speed, self.tap_interval, self.key_interval = speed, tap_interval, key_interval
        self.poll_interval, self.timeout = poll_interval, timeout
        self.key_checkpoint = key_checkpoint
        self.initial_display = None
        self.window = None
        self.last_action = 0
        self.sequence = 0
        self.report = {"name": spec.get("name", "mobile scenario"), "cases": [],
                       "speed": speed, "tapInterval": tap_interval / speed,
                       "keyInterval": key_interval / speed, "keyCheckpoint": key_checkpoint,
                       "inputMethod": "native taps on visible software keyboard keys"}

    def observe(self):
        display = mobile.active_display(self.config["device"])
        if self.initial_display is not None and display != self.initial_display:
            raise RuntimeError("Active display or orientation changed during scenario")
        foreground = mobile.rpc(self.config, "device.apps.foreground", {"fields": []})
        if foreground.get("bundleId") != self.spec["bundleId"]:
            raise RuntimeError("Unexpected foreground app: " + str(foreground))
        raw = mobile.rpc(self.config, "device.dump.ui", {"format": "raw"})
        nodes = list(mobile.walk(raw))
        windows = [n for n in nodes if n.get("elementType") == 4
                   and n.get("displayID") == display["displayId"]]
        if not windows:
            raise RuntimeError("No AX window on active display")
        window = max(windows, key=lambda n: n["frame"]["Width"] * n["frame"]["Height"])["frame"]
        if self.window is not None and window != self.window:
            raise RuntimeError("Window geometry changed during scenario")
        self.initial_display, self.window = display, window
        viewport = self.spec.get("viewport")
        if viewport and any(window[k] != viewport[k.lower()] for k in ("Width", "Height")):
            raise RuntimeError("Scenario coordinate viewport does not match this device")
        return {"display": display, "foreground": foreground, "raw": raw}

    def visible(self, node, observation):
        if node.get("displayID") != observation["display"]["displayId"] or node.get("enabled") is False:
            return False
        f = node.get("frame", {})
        if f.get("Width", 0) <= 0 or f.get("Height", 0) <= 0:
            return False
        x, y = center(node, self.window)
        return 0 <= x < self.window["Width"] and 0 <= y < self.window["Height"]

    def matches(self, node, selector):
        return all(node.get(k) == v for k, v in selector.items())

    def targets(self, observation, selector, keyboard=False):
        root = observation["raw"]
        if keyboard:
            roots = [n for n in mobile.walk(root) if n.get("elementType") == 19
                     and n.get("displayID") == observation["display"]["displayId"]]
        else:
            roots = [root]
        found = {}
        for root in roots:
            for n in mobile.walk(root):
                if self.matches(n, selector) and self.visible(n, observation):
                    found[center(n, self.window)] = n
        if len(found) != 1:
            raise RuntimeError(f"Expected one visible target {selector}, found {len(found)}")
        return next(iter(found))

    def condition(self, observation, expected):
        nodes = [n for n in mobile.walk(observation["raw"])
                 if n.get("displayID") == observation["display"]["displayId"]]
        if "label" in expected and not any(self.matches(n, {"label": expected["label"]})
                                            and self.visible(n, observation) for n in nodes):
            return False
        keyboard = any(n.get("elementType") == 19 for n in nodes)
        focused = [n for n in nodes if n.get("hasFocus") and n.get("elementType") in (45, 49, 50, 52)]
        if "keyboard" in expected and keyboard != expected["keyboard"]:
            return False
        if "focused" in expected and bool(focused) != expected["focused"]:
            return False
        if "focusedValue" in expected and not any((n.get("value") or "") == expected["focusedValue"] for n in focused):
            return False
        return True

    def wait(self, expected, name, timeout=None):
        deadline = time.monotonic() + (self.timeout if timeout is None else timeout)
        attempts = 0
        while True:
            attempts += 1
            observation = self.observe()
            if self.condition(observation, expected):
                mobile.snapshot(self.run, self.config, name, observation=observation)
                return observation, attempts
            if time.monotonic() >= deadline:
                mobile.snapshot(self.run, self.config, name + "-failed", observation=observation)
                raise TimeoutError("Condition not met: " + str(expected))
            time.sleep(min(self.poll_interval, max(0, deadline - time.monotonic())))

    def tap(self, point, interval, key_label=None):
        x, y = point
        if not (0 <= x < self.window["Width"] and 0 <= y < self.window["Height"]):
            raise ValueError("Tap outside the observed viewport")
        display = mobile.active_display(self.config["device"])
        if display != self.initial_display:
            raise RuntimeError("Display changed before tap")
        foreground = mobile.rpc(self.config, "device.apps.foreground", {"fields": []})
        if foreground.get("bundleId") != self.spec["bundleId"]:
            raise RuntimeError("Foreground changed before tap")
        remaining = interval / self.speed - (time.monotonic() - self.last_action)
        if remaining > 0:
            time.sleep(remaining)
        params = {"x": x, "y": y, "displayID": display["displayId"]}
        probe = mobile.rpc(self.config, "device.io.tap", {**params, "dryRun": True})
        if (probe.get("executed") is not False or not probe.get("displayTargetingAvailable")
                or probe.get("selectedDisplayID") != params["displayID"]):
            raise RuntimeError("Runner did not confirm display targeting")
        mobile.event(self.run, "action-start", method="device.io.tap", params=params, keyLabel=key_label)
        self.last_action = time.monotonic()
        result = mobile.rpc(self.config, "device.io.tap", params)
        if not result.get("success") or not result.get("executed"):
            raise RuntimeError("Tap was not executed")
        mobile.event(self.run, "action-result", method="device.io.tap", result=result, keyLabel=key_label)

    def step(self, step, name):
        action = step["action"]
        if action == "wait":
            _, attempts = self.wait(step["expect"], name, step.get("timeout"))
            return {"attempts": attempts}
        observation = self.observe()
        if action == "tap":
            point = step.get("point")
            if point is not None and not self.spec.get("viewport"):
                raise ValueError("Coordinate taps require an explicit viewport guard")
            if point is None:
                point = self.targets(observation, step["selector"])
            self.tap(point, self.tap_interval)
            return {}
        if action == "keys":
            if not self.condition(observation, {"keyboard": True, "focused": True}):
                raise RuntimeError("Expected visible keyboard and focused text input")
            batch = self.key_checkpoint == "batch" and step.get("stableLayout") is True
            labels = step["labels"]
            points = [self.targets(observation, {"label": label, "elementType": 20}, keyboard=True)
                      for label in labels] if batch else None
            started = time.monotonic()
            for i, label in enumerate(labels):
                point = points[i] if batch else self.targets(observation, {"label": label, "elementType": 20}, keyboard=True)
                self.tap(point, self.key_interval, key_label=label)
                if not batch:
                    observation = self.observe()
                    mobile.snapshot(self.run, self.config, name + f"-key-{i+1}", observation=observation)
                    values = step.get("expectedValues")
                    if values and not self.condition(observation, {"focusedValue": values[i]}):
                        raise AssertionError("Unexpected composed value after key " + label)
            key_seconds = time.monotonic() - started
            _, attempts = self.wait({"keyboard": True, "focusedValue": step["expected"]}, name)
            return {"keyTapSeconds": key_seconds, "keys": labels, "mode": "batch" if batch else "each",
                    "expected": step["expected"], "attempts": attempts}
        raise ValueError("Unsupported action " + action)

    def execute(self):
        if not self.config.get("displayTapSupported"):
            raise RuntimeError("Verify the display tap patch before scenario execution")
        started = time.monotonic()
        self.report["startedAt"] = time.time()
        mobile.event(self.run, "scenario-start", name=self.report["name"], speed=self.speed)
        failed = False
        try:
            for case_index, case in enumerate(self.spec["cases"], 1):
                result = {"name": case["name"], "steps": [], "status": "running"}
                self.report["cases"].append(result)
                case_start = time.monotonic()
                for step_index, step in enumerate(case["steps"], 1):
                    name = f"scenario-{case_index}-{step_index}"
                    step_start = time.monotonic()
                    entry = {"action": step["action"], "artifact": name}
                    result["steps"].append(entry)
                    try:
                        entry.update(self.step(step, name))
                        entry["status"] = "passed"
                    except Exception as error:
                        entry.update(status="failed", error=str(error))
                        raise
                    finally:
                        entry["seconds"] = round(time.monotonic() - step_start, 3)
                        mobile.save(self.run / "scenario-results.json", self.report)
                result.update(status="passed", seconds=round(time.monotonic() - case_start, 3))
                print(f"PASS {case['name']} ({result['seconds']}s)", flush=True)
        except Exception as error:
            failed = True
            self.report["error"] = str(error)
            if self.report["cases"]:
                self.report["cases"][-1]["status"] = "failed"
            print("FAIL " + str(error), flush=True)
        finally:
            self.report.update(status="failed" if failed else "passed",
                               seconds=round(time.monotonic() - started, 3), endedAt=time.time())
            mobile.save(self.run / "scenario-results.json", self.report)
            mobile.event(self.run, "scenario-stop", status=self.report["status"], seconds=self.report["seconds"])
        return 1 if failed else 0


def validate(spec):
    if not isinstance(spec.get("bundleId"), str) or not spec.get("cases"):
        raise ValueError("bundleId and nonempty cases are required")
    for case in spec["cases"]:
        if not case.get("name") or not case.get("steps"):
            raise ValueError("Each case needs a name and nonempty steps")
        for step in case["steps"]:
            if step.get("action") not in ("tap", "wait", "keys"):
                raise ValueError("Supported actions: tap, wait, keys")
            if step["action"] == "wait":
                allowed = {"label", "keyboard", "focused", "focusedValue"}
                if not step.get("expect") or not set(step["expect"]) <= allowed:
                    raise ValueError("Unknown or empty wait condition")
                if "timeout" in step and (not math.isfinite(step["timeout"]) or not 0 < step["timeout"] <= 120):
                    raise ValueError("Timeout must be 0..120 seconds")
            if step["action"] == "keys":
                if not step.get("labels") or not isinstance(step.get("expected"), str):
                    raise ValueError("keys requires labels and expected final value")
                if step.get("expectedValues") and len(step["expectedValues"]) != len(step["labels"]):
                    raise ValueError("expectedValues must match labels length")
            if step["action"] == "tap" and ("point" in step) == ("selector" in step):
                raise ValueError("tap needs exactly one of selector or point")


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--run", type=Path, required=True)
    p.add_argument("--scenario", type=Path, required=True)
    p.add_argument("--speed", type=float, default=1)
    p.add_argument("--tap-interval", type=float, default=.2)
    p.add_argument("--key-interval", type=float, default=.12)
    p.add_argument("--poll-interval", type=float, default=.25)
    p.add_argument("--timeout", type=float, default=15)
    p.add_argument("--key-checkpoint", choices=("batch", "each"), default="batch")
    p.add_argument("--validate-only", action="store_true")
    a = p.parse_args()
    for name in ("speed", "tap_interval", "key_interval", "poll_interval", "timeout"):
        value = getattr(a, name)
        if not math.isfinite(value) or value < 0 or (name in ("speed", "poll_interval", "timeout") and value == 0):
            p.error(name + " must be a finite positive number (intervals may be zero)")
    if a.timeout > 120:
        p.error("timeout must be at most 120 seconds")
    spec = json.loads(a.scenario.read_text())
    validate(spec)
    if a.validate_only:
        print("Scenario is valid")
        return 0
    run = a.run.expanduser().resolve()
    if (run / "scenario-results.json").exists():
        p.error("Scenario result already exists; use a fresh run")
    config = json.loads((run / "session.json").read_text())
    key = hashlib.sha256(config["device"].encode()).hexdigest()[:16]
    lock = open(Path(tempfile.gettempdir()) / ("mobile-scenario-" + key + ".lock"), "w")
    fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
    mobile.save(run / "scenario.json", spec)
    runner = ScenarioRunner(run, config, spec, a.speed, a.tap_interval, a.key_interval,
                            a.poll_interval, a.timeout, a.key_checkpoint)
    return runner.execute()


if __name__ == "__main__":
    raise SystemExit(main())
