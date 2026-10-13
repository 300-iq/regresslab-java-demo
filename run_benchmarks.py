#!/usr/bin/env python3
"""Fixed, credential-free JMH producer. JSON input is metadata, never executable."""
import argparse
import datetime as dt
import hashlib
import json
import math
import os
import pathlib
import platform
import re
import signal
import subprocess
import sys
import time

CASES = ["dev.regresslab.demo.EventBenchmarks.countByCategory", "dev.regresslab.demo.EventBenchmarks.distinctCategories"]
FORKS, WARMUPS, ITERATIONS, TIME_MS = 3, 3, 5, 1000
JVM_ARGS = ["-Xms256m", "-Xmx256m"]

def stamp():
    return dt.datetime.now(dt.timezone.utc).isoformat().replace("+00:00", "Z")

def clean_environment():
    # No Actions control-file variables, tokens, checkout credentials or user Maven settings.
    return {key: value for key, value in os.environ.items() if key in {"PATH", "JAVA_HOME", "LANG", "LC_ALL", "HOME", "TMPDIR"}}

def execute(command, cwd, log, seconds):
    started, clock = stamp(), time.monotonic()
    with open(log, "wb") as output:
        process = subprocess.Popen(command, cwd=cwd, env=clean_environment(), stdout=output,
                                   stderr=subprocess.STDOUT, start_new_session=True)
        try:
            code = process.wait(timeout=seconds)
            status = "COMPLETE" if code == 0 else "BENCHMARK_FAILED"
        except subprocess.TimeoutExpired:
            os.killpg(process.pid, signal.SIGKILL)
            code = process.wait()
            status = "TIMEOUT"
    return {"status": status, "exitCode": code, "startedAt": started, "finishedAt": stamp(),
            "elapsedMs": max(0, round((time.monotonic() - clock) * 1000)), "error": None}

def parse_jmh(path, complete=True):
    raw = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(raw, list) or len(raw) > 2:
        raise ValueError("Unexpected JMH case list")
    output = []
    for case in raw:
        name = case["benchmark"]
        if name not in CASES or any(c["identifier"] == name for c in output):
            raise ValueError("Unknown/duplicate JMH case")
        if case["jmhVersion"] != "1.37" or case["mode"] != "avgt" or case["threads"] != 1 or case["forks"] != 1:
            raise ValueError("Unexpected JMH framework/mode/fork/thread settings")
        if case["warmupIterations"] != WARMUPS or case["measurementIterations"] != ITERATIONS:
            raise ValueError("Unexpected JMH iteration configuration")
        if case["warmupTime"] != "1 s" or case["measurementTime"] != "1 s" or case["params"] != {"events": "10000"}:
            raise ValueError("Unexpected JMH timing/input settings")
        metric = case["primaryMetric"]
        if metric["scoreUnit"] != "ns/op" or len(metric["rawData"]) != 1:
            raise ValueError("Missing per-fork raw ns/op data")
        values = metric["rawData"][0]
        if len(values) != ITERATIONS or any(type(v) not in (int, float) or not math.isfinite(v) or v <= 0 or v > 1e15 for v in values):
            raise ValueError("Invalid raw observations")
        output.append({"identifier": name, "unit": "ns/op", "values": values})
    if complete and sorted(c["identifier"] for c in output) != CASES:
        raise ValueError("Incomplete JMH output")
    return output

def order(run_id):
    first = "BASELINE" if run_id % 2 == 0 else "CANDIDATE"
    other = "CANDIDATE" if first == "BASELINE" else "BASELINE"
    return [arm for fork in range(FORKS) for arm in ((first, other) if fork % 2 == 0 else (other, first))]

def environment():
    version = subprocess.run(["java", "-XshowSettings:properties", "-version"], capture_output=True, text=True, check=True, env=clean_environment())
    java = re.search(r"java.version = (\S+)", version.stderr).group(1)
    if not re.fullmatch(r"21(?:[.+_-].*)?", java):
        raise ValueError("Java 21 is required")
    cpu = platform.processor()
    if pathlib.Path("/proc/cpuinfo").exists():
        for line in pathlib.Path("/proc/cpuinfo").read_text().splitlines():
            if line.startswith("model name"):
                cpu = line.split(":", 1)[1].strip(); break
    elif platform.system() == "Darwin":
        cpu = subprocess.check_output(["sysctl", "-n", "machdep.cpu.brand_string"], text=True).strip()
    architecture = {"arm64": "aarch64", "aarch64": "aarch64", "x86_64": "x86_64"}[platform.machine()]
    return {"runner": f"{os.environ.get('RUNNER_ENVIRONMENT', 'local')}:{os.environ.get('RUNNER_NAME', platform.node())}"[:200],
            "os": f"{platform.system()} {platform.release()}"[:200], "architecture": architecture,
            "cpu": (cpu or "CPU identity unavailable")[:200], "javaVersion": java, "jmhVersion": "1.37"}

def produce(baseline, candidate, destination, event, run_id, attempt):
    pr, repository = event["pull_request"], event["repository"]
    baseline_sha, candidate_sha = pr["base"]["sha"], pr["head"]["sha"]
    for arm, directory, sha in [("BASELINE", baseline, baseline_sha), ("CANDIDATE", candidate, candidate_sha)]:
        if not re.fullmatch("[0-9a-f]{40}", sha): raise ValueError("Invalid commit SHA")
        actual = subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=directory, text=True).strip()
        if actual != sha: raise ValueError(f"{arm} checkout does not match event SHA")
    if baseline_sha == candidate_sha: raise ValueError("Commit pair must differ")
    destination.mkdir(parents=True, exist_ok=True)
    logs = destination / "logs"; logs.mkdir(exist_ok=True)
    runner_env = environment()
    harness = pathlib.Path(__file__).parent / "src/main/java/dev/regresslab/demo/EventBenchmarks.java"
    harness_hash = hashlib.sha256(harness.read_bytes()).hexdigest()
    args = JVM_ARGS + [f"-Dregresslab.harness={harness_hash}"]
    ledger = {"schemaVersion": 1, "strategy": "alternating-forks-v1", "harnessSha256": harness_hash,
              "order": order(run_id), "builds": {}, "blocks": []}
    for arm, directory in [("BASELINE", baseline), ("CANDIDATE", candidate)]:
        # Use the base's fixed reviewed harness for both app commits.
        (directory / "src/main/java/dev/regresslab/demo/EventBenchmarks.java").write_bytes(harness.read_bytes())
        settings = logs / f"{arm.lower()}-settings.xml"
        settings.write_text("<settings xmlns=\"http://maven.apache.org/SETTINGS/1.0.0\"/>")
        build = execute(["mvn", "-B", "-ntp", "--settings", str(settings.resolve()), "-Dmaven.repo.local=" + str((logs / "m2").resolve()), "package"], directory, logs / f"{arm.lower()}-build.log", 240)
        if build["status"] == "BENCHMARK_FAILED": build["status"] = "BUILD_FAILED"
        if build["status"] == "COMPLETE":
            check = execute(["java", "-cp", "target/test-classes:target/classes", "dev.regresslab.demo.EventStatisticsTest"], directory, logs / f"{arm.lower()}-correctness.log", 20)
            if check["status"] != "COMPLETE": build = {**check, "status": "BUILD_FAILED", "error": "Correctness check failed"}
        ledger["builds"][arm] = build
    indices = {"BASELINE": 0, "CANDIDATE": 0}
    for arm in ledger["order"]:
        index = indices[arm]; indices[arm] += 1
        directory = baseline if arm == "BASELINE" else candidate
        result_path = logs / f"{arm.lower()}-{index}-jmh.json"
        block = {"arm": arm, "forkIndex": index, "observations": []}
        if ledger["builds"][arm]["status"] != "COMPLETE" or any(b["arm"] == arm and b["status"] != "COMPLETE" for b in ledger["blocks"]):
            block.update(status="NOT_RUN", exitCode=None, startedAt=None, finishedAt=None, elapsedMs=0, error="Build or earlier block did not complete")
        else:
            result = execute(["java", "-jar", "target/benchmarks.jar", "dev.regresslab.demo.EventBenchmarks.*", "-bm", "avgt", "-tu", "ns", "-f", "1", "-wi", str(WARMUPS), "-i", str(ITERATIONS), "-w", "1s", "-r", "1s", "-t", "1", "-foe", "true", "-jvmArgs", " ".join(args), "-rf", "json", "-rff", str(result_path.resolve())], directory, logs / f"{arm.lower()}-{index}.log", 60)
            block.update(result)
            try:
                block["observations"] = parse_jmh(result_path, complete=result["status"] == "COMPLETE")
            except (OSError, ValueError, KeyError, TypeError) as error:
                if block["status"] == "COMPLETE": block["status"] = "BENCHMARK_FAILED"
                block["error"] = f"No complete valid JMH result: {type(error).__name__}"
        ledger["blocks"].append(block)
        print(f"{arm} fork block {index}: {block['status']}", flush=True)
    failed = False
    for arm, sha, branch in [("BASELINE", baseline_sha, pr["base"]["ref"]), ("CANDIDATE", candidate_sha, pr["head"]["ref"])]:
        blocks = [block for block in ledger["blocks"] if block["arm"] == arm]
        complete = ledger["builds"][arm]["status"] == "COMPLETE" and all(block["status"] == "COMPLETE" for block in blocks)
        cases = []
        if complete:
            for name in CASES:
                samples = [{"forkIndex": block["forkIndex"], "iterationIndex": i, "value": value} for block in blocks for case in block["observations"] if case["identifier"] == name for i, value in enumerate(case["values"])]
                cases.append({"identifier": name, "metricName": "average_time", "unit": "ns/op", "direction": "LOWER", "parameters": {"events": "10000"}, "configuration": {"forkCount": FORKS, "warmupIterations": WARMUPS, "measurementIterations": ITERATIONS, "warmupTimeMs": TIME_MS, "measurementTimeMs": TIME_MS, "jvmArgs": args}, "samples": samples})
        failed |= not complete
        run = {"schemaVersion": 2, "artifactId": f"github-{run_id}-{attempt}-{arm.lower()}", "dataOrigin": "REAL",
               "repository": {"githubId": repository["id"], "owner": repository["owner"]["login"], "name": repository["name"]},
               "pullRequest": {"number": pr["number"], "title": pr["title"], "state": "OPEN", "baselineSha": baseline_sha, "candidateSha": candidate_sha},
               "run": {"workflowRunId": run_id, "runAttempt": attempt, "commitSha": sha, "branch": branch, "measuredAt": next((b["startedAt"] for b in blocks if b["startedAt"]), stamp()), "status": "COMPLETE" if complete else "FAILED", "environment": runner_env}, "benchmarks": cases}
        output = destination / arm.lower(); output.mkdir(exist_ok=True)
        (output / "run.json").write_text(json.dumps(run, indent=2, allow_nan=False) + "\n")
        (output / "execution.json").write_text(json.dumps(ledger, indent=2, allow_nan=False) + "\n")
    return 1 if failed else 0

if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--baseline", type=pathlib.Path, required=True)
    parser.add_argument("--candidate", type=pathlib.Path, required=True)
    parser.add_argument("--output", type=pathlib.Path, required=True)
    parser.add_argument("--event", type=pathlib.Path, default=os.environ.get("GITHUB_EVENT_PATH"))
    options = parser.parse_args()
    event = json.loads(options.event.read_text())
    sys.exit(produce(options.baseline.resolve(), options.candidate.resolve(), options.output.resolve(), event,
                     int(os.environ["GITHUB_RUN_ID"]), int(os.environ.get("GITHUB_RUN_ATTEMPT", "1"))))
