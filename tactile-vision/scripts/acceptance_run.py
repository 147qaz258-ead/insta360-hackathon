import base64
import json
import sys
import time
import urllib.request

BASE = "http://127.0.0.1:8765"
IMAGE = r"samples\e2e_photo.png"


def post(url, payload):
    body = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(url, data=body, method="POST",
                                 headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=60) as resp:
        return json.loads(resp.read().decode("utf-8"))


def get(url):
    with urllib.request.urlopen(url, timeout=30) as resp:
        return json.loads(resp.read().decode("utf-8"))


def main():
    data_url = "data:image/png;base64," + base64.b64encode(open(IMAGE, "rb").read()).decode("ascii")
    created = post(f"{BASE}/api/agent/runs", {"image_data_url": data_url, "user_instruction": ""})
    run_id = created["run_id"]
    print(f"run_id={run_id}", flush=True)
    last_sig = None
    deadline = time.time() + 1500
    while time.time() < deadline:
        snap = get(f"{BASE}/api/agent/runs/{run_id}")
        sig = (snap.get("state"), snap.get("candidate"), snap.get("message"))
        if sig != last_sig:
            print(f"[{time.strftime('%H:%M:%S')}] state={sig[0]} candidate={sig[1]} msg={sig[2]}", flush=True)
            last_sig = sig
        if snap.get("state") in {"READY", "ERROR", "CANCELLED"}:
            trace = snap.get("trace") or []
            print(f"FINAL state={snap['state']} candidates={snap.get('candidate_count')} "
                  f"frame_id={snap.get('frame_id')} checksum={snap.get('checksum')}", flush=True)
            for entry in trace:
                print(json.dumps({
                    "candidate": entry.get("candidate"),
                    "decision": entry.get("decision"),
                    "stages": entry.get("stages"),
                    "metrics": entry.get("metrics"),
                    "scores": (entry.get("critique") or {}).get("scores"),
                    "critical_defects": (entry.get("critique") or {}).get("critical_defects"),
                    "density_verdict": (entry.get("critique") or {}).get("density_verdict"),
                    "rationale": (entry.get("critique") or {}).get("rationale"),
                    "blind_read": entry.get("blind_read"),
                    "review_token": entry.get("review_token"),
                    "validation_errors": (entry.get("validation") or {}).get("errors", [])[:5],
                    "design_notes": (entry.get("design_notes") or "")[:300],
                }, ensure_ascii=False, indent=1), flush=True)
            return 0 if snap.get("state") == "READY" else 2
        time.sleep(5)
    print("TIMEOUT waiting for run to finish", flush=True)
    return 3


if __name__ == "__main__":
    sys.exit(main())
