#!/usr/bin/env python3
"""Check disposable data; seed/verify/cleanup also support restart testing."""
import json
import os
from pathlib import Path
import subprocess
import sys
import urllib.request
import uuid

root = Path(__file__).resolve().parent.parent
os.chdir(root)
# Keep resolved credentials only in memory; let Compose parse its own dotenv syntax.
resolved = subprocess.run(["docker", "compose", "config", "--format", "json"],
                          text=True, capture_output=True)
if resolved.returncode:
    raise SystemExit("Compose configuration is invalid; run docker compose config --quiet")
services = json.loads(resolved.stdout)["services"]
config = {**services["mysql"]["environment"], **services["minio"]["environment"]}
config["FASTAPI_PORT"] = str(services["fastapi"]["ports"][0]["published"])
state = root / ".smoke-state.json"
mode = sys.argv[1] if len(sys.argv) > 1 else "all"
if mode not in ("all", "seed", "verify", "cleanup"):
    raise SystemExit("Usage: smoke.py [all|seed|verify|cleanup]")
if mode in ("all", "seed"):
    if state.exists():
        raise SystemExit("A probe is pending; verify or cleanup first.")
    token = uuid.uuid4().hex
    state.write_text(json.dumps({"token": token}))
else:
    token = json.loads(state.read_text())["token"]

def probe(stage):
    data = {key: config[key] for key in (
        "MYSQL_USER", "MYSQL_PASSWORD", "MYSQL_DATABASE", "MINIO_ROOT_USER", "MINIO_ROOT_PASSWORD")}
    data.update(mode=stage, token=token)
    result = subprocess.run(["docker", "compose", "exec", "-T", "fastapi", "python", "probe.py"],
                            input=json.dumps(data), text=True, capture_output=True)
    if result.returncode:
        # Avoid echoing driver error messages that might contain credentials.
        raise RuntimeError(stage + " failed; inspect service health and configuration privately")
    print(result.stdout.strip())

try:
    if mode == "all":
        for path in ("/health", "/ready", "/docs", "/openapi.json"):
            url = "http://127.0.0.1:" + config["FASTAPI_PORT"] + path
            with urllib.request.urlopen(url, timeout=10) as response:
                if response.status != 200:
                    raise RuntimeError("API check failed")
            print(path + ": HTTP 200")
        try:
            probe("seed")
            probe("verify")
        finally:
            probe("cleanup")
        state.unlink()
    else:
        probe(mode)
        if mode == "cleanup":
            state.unlink()
except Exception as exc:
    print("CHECK FAILED: " + str(exc), file=sys.stderr)
    print("Probe state retained for diagnosis/cleanup: .smoke-state.json", file=sys.stderr)
    raise SystemExit(1)
