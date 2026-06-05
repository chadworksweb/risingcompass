"""Faultline -- agent runner harness (Phase 4).

A thin loop that drives the error ledger through its fix lifecycle by calling the
agent API (`/api/agent/faultline/*`). The harness owns the mechanical parts --
polling the queue, leasing a fault, heartbeating, posting the work log,
advancing status, releasing -- so the only thing you plug in is the INTELLIGENCE:
replace `diagnose()` with a Claude Code invocation or an Agent SDK worker that
reads the traceback + code pointers and decides what to do.

Stdlib only (urllib) so it runs anywhere -- by hand, by cron, or from a
/schedule routine. The API ships dark until RC_ERROR_AGENT_KEY is set on the
server AND passed here.

Usage:
    set RC_ERROR_AGENT_KEY=...                 (must match the server)
    python scripts/faultline_agent.py --once   # work one fault and exit
    python scripts/faultline_agent.py --loop --interval 60 --env prod

Config (env or flags):
    FAULTLINE_BASE_URL   default http://127.0.0.1:8000
    RC_ERROR_AGENT_KEY   required (X-Error-Agent-Key)
    FAULTLINE_WORKER_ID  default faultline-agent-<host>
"""

import argparse
import json
import os
import socket
import time
import urllib.error
import urllib.request


class FaultlineClient:
    def __init__(self, base_url: str, agent_key: str):
        self.base = base_url.rstrip("/")
        self.key = agent_key

    def _req(self, method: str, path: str, body: dict | None = None) -> dict:
        url = self.base + "/api/agent/faultline" + path
        data = json.dumps(body).encode("utf-8") if body is not None else None
        req = urllib.request.Request(url, data=data, method=method)
        req.add_header("X-Error-Agent-Key", self.key)
        if data is not None:
            req.add_header("Content-Type", "application/json")
        try:
            with urllib.request.urlopen(req, timeout=30) as resp:
                raw = resp.read().decode("utf-8")
                return json.loads(raw) if raw else {}
        except urllib.error.HTTPError as e:
            detail = e.read().decode("utf-8", "replace")
            raise RuntimeError(f"{method} {path} -> HTTP {e.code}: {detail}") from None

    def queue(self, env=None, limit=10):
        qs = f"?limit={limit}" + (f"&environment={env}" if env else "")
        return self._req("GET", "/queue" + qs)["queue"]

    def detail(self, sig_id):
        return self._req("GET", f"/signatures/{sig_id}")

    def claim(self, sig_id, worker_id, ttl=900):
        return self._req("POST", f"/signatures/{sig_id}/claim",
                         {"worker_id": worker_id, "ttl_seconds": ttl})

    def heartbeat(self, sig_id, worker_id, ttl=900):
        return self._req("POST", f"/signatures/{sig_id}/heartbeat",
                         {"worker_id": worker_id, "ttl_seconds": ttl})

    def release(self, sig_id, worker_id):
        return self._req("POST", f"/signatures/{sig_id}/release", {"worker_id": worker_id})

    def action(self, sig_id, action_type, note, payload=None, agent_model=None, idem=None):
        return self._req("POST", f"/signatures/{sig_id}/actions", {
            "action_type": action_type, "note": note, "payload": payload,
            "agent_model": agent_model, "idempotency_key": idem,
        })

    def triage(self, sig_id, **kw):
        return self._req("POST", f"/signatures/{sig_id}/triage", kw)

    def status(self, sig_id, to_status, note=None, resolution=None, idem=None):
        return self._req("POST", f"/signatures/{sig_id}/status", {
            "to_status": to_status, "note": note, "resolution": resolution,
            "idempotency_key": idem,
        })


# ---------------------------------------------------------------------------
# PLUG YOUR AGENT IN HERE.
#
# `fault` is the full detail dict (title, last_traceback, last_context,
# code_pointers, occurrences, actions, ...). Return a dict describing what to do:
#   {
#     "severity": "high",            # optional re-triage
#     "area": "calibration",         # optional
#     "actions": [                   # work-log entries to append, in order
#       {"type": "diagnosis", "note": "Root cause: ..."},
#       {"type": "proposed_fix", "note": "Patch X in file Y", "payload": {...}},
#     ],
#     "to_status": "investigating",  # optional status advance
#   }
#
# The default below is a SKELETON: it just records that the agent looked at the
# fault and where the code pointers lead. Swap the body for a real call -- e.g.
# shell out to `claude -p` with the traceback, or call the Agent SDK.
# ---------------------------------------------------------------------------

def diagnose(fault: dict) -> dict:
    pointers = fault.get("code_pointers") or []
    top = pointers[-1] if pointers else None
    where = f"{top['file']}:{top['line']} in {top['func']}" if top else "unknown"
    note = (
        "Auto-triage stub looked at this fault. "
        f"Innermost frame: {where}. "
        f"{len(fault.get('occurrences') or [])} recent occurrence(s). "
        "Plug a real agent into diagnose() to investigate + propose a fix."
    )
    return {
        "actions": [{"type": "diagnosis", "note": note}],
        # Intentionally no status advance in the stub -- a real agent decides.
    }


def work_one(client: FaultlineClient, fault_summary: dict, worker_id: str, model: str) -> bool:
    sig_id = fault_summary["id"]
    try:
        client.claim(sig_id, worker_id)
    except RuntimeError as e:
        print(f"  skip #{sig_id}: {e}")
        return False
    try:
        fault = client.detail(sig_id)
        print(f"  working #{sig_id} [{fault.get('severity')}] {fault.get('title')}")
        plan = diagnose(fault) or {}

        if plan.get("severity") or plan.get("area"):
            client.triage(sig_id, severity=plan.get("severity"), area=plan.get("area"))
        for i, act in enumerate(plan.get("actions") or []):
            client.action(sig_id, act.get("type", "comment"), act.get("note", ""),
                          payload=act.get("payload"), agent_model=model,
                          idem=f"{worker_id}:{sig_id}:{i}:{act.get('type')}")
        if plan.get("to_status"):
            client.status(sig_id, plan["to_status"], note=plan.get("status_note"),
                          resolution=plan.get("resolution"))
        return True
    finally:
        try:
            client.release(sig_id, worker_id)
        except RuntimeError:
            pass


def main():
    ap = argparse.ArgumentParser(description="Faultline agent runner")
    ap.add_argument("--base-url", default=os.environ.get("FAULTLINE_BASE_URL", "http://127.0.0.1:8000"))
    ap.add_argument("--worker", default=os.environ.get("FAULTLINE_WORKER_ID", f"faultline-agent-{socket.gethostname()}"))
    ap.add_argument("--model", default=os.environ.get("AGENT_MODEL", "claude-opus-4-6"))
    ap.add_argument("--env", default=None, help="filter queue by environment (local|prod)")
    ap.add_argument("--batch", type=int, default=5, help="max faults per pass")
    ap.add_argument("--once", action="store_true", help="one pass then exit")
    ap.add_argument("--loop", action="store_true", help="poll forever")
    ap.add_argument("--interval", type=int, default=60, help="seconds between passes in --loop")
    args = ap.parse_args()

    key = os.environ.get("RC_ERROR_AGENT_KEY", "")
    if not key:
        raise SystemExit("RC_ERROR_AGENT_KEY is required (must match the server).")
    client = FaultlineClient(args.base_url, key)

    def one_pass():
        q = client.queue(env=args.env, limit=args.batch)
        if not q:
            print("queue empty.")
            return 0
        print(f"queue: {len(q)} fault(s)")
        done = 0
        for fs in q:
            if work_one(client, fs, args.worker, args.model):
                done += 1
        return done

    if args.loop:
        print(f"Faultline agent '{args.worker}' looping every {args.interval}s. Ctrl-C to stop.")
        while True:
            try:
                one_pass()
            except Exception as e:
                print(f"pass error: {e}")
            time.sleep(args.interval)
    else:
        n = one_pass()
        print(f"done. worked {n} fault(s).")


if __name__ == "__main__":
    main()
