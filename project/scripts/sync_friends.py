#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""One-command incremental sync for friend Moments exports.

Pipeline (per run):
  1. snapshot live sns.db/-wal/-shm (append-only, never overwrites old snapshots)
  2. merge-export target(s): union of the legacy plaintext DB + every snapshot + previous JSON
     -> only adds/updates; posts that disappeared from WeChat are kept and marked archived
  3. rebuild HTML / Markdown / CSV / XLSX from the merged JSON
  4. verify the rebuilt deliverables

Usage:
  python3 sync_friends.py                         # all targets, with snapshot
  python3 sync_friends.py --targets lili,caimiaohan
  python3 sync_friends.py --no-snapshot --targets zhaohaiyi   # re-export from existing snapshots
"""
import argparse
import json
import os
import subprocess
import sys
import time
from datetime import datetime

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import wxconfig

SCRIPTS = os.path.dirname(os.path.abspath(__file__))
PROJECT = os.path.dirname(SCRIPTS)
ROOT = os.path.dirname(PROJECT)
LOGS = os.path.join(PROJECT, "logs")
TZ = None

def now_iso():
    return datetime.now().astimezone().isoformat(timespec="seconds")

def log(msg):
    print("[sync %s] %s" % (datetime.now().strftime("%H:%M:%S"), msg), flush=True)

def run(cmd, env=None):
    log("$ " + " ".join(cmd))
    t0 = time.time()
    r = subprocess.run(cmd, cwd=SCRIPTS, env=env)
    log("exit=%d in %.1fs" % (r.returncode, time.time() - t0))
    return r.returncode

def main():
    ap = argparse.ArgumentParser(description="incremental snapshot + merge export + rebuild reports")
    ap.add_argument("--targets", default="", help="comma separated keys in config targets; default all")
    ap.add_argument("--no-snapshot", action="store_true", help="skip taking a new snapshot")
    ap.add_argument("--skip-reports", action="store_true")
    ap.add_argument("--skip-verify", action="store_true")
    ap.add_argument("--workers", type=int, default=None,
                    help="concurrent downloads (default: config export.workers)")
    a = ap.parse_args()
    global LOGS
    LOGS = wxconfig.paths()["logs_dir"]
    if not a.workers:
        a.workers = wxconfig.workers(8)
    targets = wxconfig.targets()
    keys = [k.strip() for k in a.targets.split(",") if k.strip()] or list(targets)
    for k in keys:
        if k not in targets:
            sys.exit("unknown target %r (known: %s)" % (k, ", ".join(targets)))

    os.makedirs(LOGS, exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    summary = {"started_at": now_iso(), "targets": {}, "snapshot": None}

    if not a.no_snapshot:
        label = "sync-" + datetime.now().strftime("%Y-%m-%d")
        rc = run([sys.executable, "-u", "snapshot_db.py", "--label", label])
        summary["snapshot"] = {"label": label, "exit": rc}
        if rc != 0:
            log("WARNING: snapshot failed; continuing with existing snapshots")
    else:
        log("skipping snapshot (--no-snapshot)")

    for k in keys:
        t = targets[k]
        outdir = t["outdir"] if os.path.isabs(t["outdir"]) else os.path.join(ROOT, t["outdir"])
        json_path = os.path.join(outdir, t["slug"] + ".json")
        log("=== target %s (%s) -> %s ===" % (k, t.get("name") or t["wxid"], outdir))
        export_cmd = [sys.executable, "-u", "export_friend.py",
                      "--wxid", t["wxid"], "--outdir", outdir,
                      "--title", t["title"], "--slug", t["slug"], "--xlsx", t["xlsx"],
                      "--workers", str(a.workers)]
        if t.get("since"):
            export_cmd += ["--since", t["since"]]
        rc = run(export_cmd)
        info = {"export_exit": rc, "outdir": outdir}
        if rc != 0:
            log("ERROR: export failed for %s; report not rebuilt" % k)
            summary["targets"][k] = info
            continue
        if not a.skip_reports:
            env = os.environ.copy()
            env["WX_DELIV"] = outdir
            env["WX_DATA"] = json_path
            rc1 = run([sys.executable, "build_report_all.py"], env=env)
            rc2 = run([sys.executable, "build_xlsx_all.py"], env=env)
            info["report_exit"] = rc1
            info["xlsx_exit"] = rc2
        if not a.skip_verify:
            info["verify_exit"] = run([sys.executable, "verify_friend.py", "--dir", outdir, "--json", json_path])
        summary["targets"][k] = info

    summary["finished_at"] = now_iso()
    out = os.path.join(LOGS, "sync_summary_%s.json" % stamp)
    json.dump(summary, open(out, "w", encoding="utf-8"), ensure_ascii=False, indent=2)
    log("summary: " + out)
    bad = [k for k, v in summary["targets"].items()
           if v.get("export_exit") or v.get("report_exit") or v.get("xlsx_exit") or v.get("verify_exit")]
    if bad:
        log("FAILED targets: " + ", ".join(bad))
        sys.exit(1)
    log("ALL DONE")

if __name__ == "__main__":
    main()
