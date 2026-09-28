"""Read-only probe runner for the tooling audit (t_1353bef3).

Runs --help / dry-run / selftest / read-only commands for the six big tools and
captures rc + output. NEVER passes --apply and never touches AUTO-MAS upstream.
"""
import datetime as dt
import io
import json
import os
import subprocess
import sys
import time

PY312 = r"C:\Users\xiach\AppData\Local\Programs\Python\Python312\python.exe"
ROOT = r"D:\1-2\MAAOrch"
OUT = r"D:\1-2\MAAOrch\docs\audits\2026-09-28_tooling_audit"
os.makedirs(OUT, exist_ok=True)

PROBES = [
    # (name, tool, argv-tail, timeout)
    ("help_run_profile", "mas_run_profile.py", ["--help"], 60),
    ("help_ondemand", "mas_ondemand.py", ["--help"], 60),
    ("help_plan_builder", "mas_plan_builder.py", ["--help"], 60),
    ("help_ondemand_mcp", "mas_ondemand_mcp.py", ["--help"], 60),
    ("help_probe_farm", "mas_probe_farm.py", ["--help"], 60),
    ("help_stage_capability", "stage_capability.py", ["--help"], 60),

    ("selftest_run_profile", "mas_run_profile.py", ["--selftest"], 600),
    ("selftest_ondemand", "mas_ondemand.py", ["--selftest"], 600),
    ("selftest_plan_builder", "mas_plan_builder.py", ["--selftest"], 600),
    ("selftest_ondemand_mcp", "mas_ondemand_mcp.py", ["--selftest"], 600),
    ("selftest_probe_farm", "mas_probe_farm.py", ["--selftest"], 600),
    ("selftest_stage_capability", "stage_capability.py", ["--selftest"], 600),

    ("run_profile_status", "mas_run_profile.py", ["--status", "--json"], 120),
    ("run_profile_list_plans", "mas_run_profile.py", ["--list-plans"], 300),
    ("run_profile_dryrun", "mas_run_profile.py",
     ["--profile", "runtime/templates/run_profile.example.json"], 300),
    ("run_profile_docclaim_dryrun_flag", "mas_run_profile.py",
     ["--profile", "runtime/templates/run_profile.example.json", "--dry-run"], 120),

    ("ondemand_list_running", "mas_ondemand.py", ["--list-running", "--json"], 400),
    ("ondemand_dryrun_account", "mas_ondemand.py", ["--account", "y9Flipped", "--json"], 300),
    ("ondemand_dryrun_script", "mas_ondemand.py", ["--script", "明日方舟-官-1-1-7", "--json"], 300),

    ("plan_builder_list_accounts", "mas_plan_builder.py", ["--list-accounts"], 120),
    ("plan_builder_dryrun", "mas_plan_builder.py",
     ["--account", "y9Flipped", "--json", "--out-dir", OUT + r"\pb_out"], 600),

    ("probe_farm_dryrun", "mas_probe_farm.py",
     ["--account", "y9Flipped", "--stage", "1-7", "--times", "2"], 900),
    ("probe_farm_list_unknown", "mas_probe_farm.py", ["--list", "unknown"], 120),

    ("stagecap_account", "stage_capability.py", ["--account", "y9Flipped", "--json"], 120),
    ("stagecap_check", "stage_capability.py", ["--check"], 300),

    ("ondemand_mcp_check", "mas_ondemand_mcp.py", ["--check"], 300),

    ("run_profile_reconcile_dry", "mas_run_profile.py", ["--reconcile", "--json"], 300),
    ("run_profile_restore_run_bogus", "mas_run_profile.py", ["--restore-run", "rp_nonexistent", "--json"], 120),
    ("ondemand_cleanup_dry", "mas_ondemand.py", ["--cleanup", "--json"], 300),
    ("ondemand_wake_dry", "mas_ondemand.py", ["--wake", "明日方舟-官-1-1-7", "--json"], 300),
    ("stagecap_rebuild_local_nowrite", "stage_capability.py", ["--rebuild-local", "--no-write"], 120),
    ("run_profile_dryrun_json", "mas_run_profile.py",
     ["--profile", "runtime/templates/run_profile.example.json", "--json"], 300),
    ("probe_farm_dryrun_json", "mas_probe_farm.py",
     ["--account", "y9Flipped", "--stage", "1-7", "--times", "2", "--json"], 900),
]

want = sys.argv[1:]
rows = []
for name, tool, tail, tmo in PROBES:
    if want and not any(w in name for w in want):
        continue
    argv = [PY312, os.path.join(ROOT, "tools", tool)] + tail
    t0 = time.time()
    try:
        p = subprocess.run(argv, capture_output=True, cwd=ROOT, timeout=tmo)
        rc, out, err = p.returncode, p.stdout.decode("utf-8", "replace"), p.stderr.decode("utf-8", "replace")
    except subprocess.TimeoutExpired as e:
        rc = "TIMEOUT"
        out = (e.stdout or b"").decode("utf-8", "replace")
        err = (e.stderr or b"").decode("utf-8", "replace")
    secs = round(time.time() - t0, 1)
    with io.open(os.path.join(OUT, name + ".txt"), "w", encoding="utf-8") as f:
        f.write("CMD: %s %s\nRC=%s SECS=%s\n%s\n--- stdout ---\n%s\n--- stderr ---\n%s\n"
                % (tool, " ".join(tail), rc, secs, "-" * 60, out, err))
    rows.append({"name": name, "rc": rc, "secs": secs, "out_head": out.strip().splitlines()[:2],
                 "err_head": err.strip().splitlines()[:2]})
    print(":: %-32s rc=%-7s %6ss" % (name, rc, secs), flush=True)

with io.open(os.path.join(OUT, "_probe_summary.json"), "w", encoding="utf-8") as f:
    json.dump({"ts": dt.datetime.now().strftime("%Y-%m-%d %H:%M:%S"), "rows": rows}, f, ensure_ascii=False, indent=1)
print("DONE", len(rows))
