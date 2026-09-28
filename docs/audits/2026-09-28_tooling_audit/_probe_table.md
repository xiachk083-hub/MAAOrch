| # | 探针 | 命令（省略 python.exe 前缀） | rc | 秒 |
| --- | --- | --- | --- | --- |
| 1 | help_ondemand | `mas_ondemand.py --help` | 0 | 1.0 |
| 2 | help_ondemand_mcp | `mas_ondemand_mcp.py --help` | 0 | 10.0 |
| 3 | help_plan_builder | `mas_plan_builder.py --help` | 0 | 0.5 |
| 4 | help_probe_farm | `mas_probe_farm.py --help` | 0 | 1.3 |
| 5 | help_run_profile | `mas_run_profile.py --help` | 0 | 1.1 |
| 6 | help_stage_capability | `stage_capability.py --help` | 0 | 0.8 |
| 7 | ondemand_cleanup_dry | `mas_ondemand.py --cleanup --json` | 0 | 0.8 |
| 8 | ondemand_dryrun_account | `mas_ondemand.py --account y9Flipped --json` | 2 | 5.1 |
| 9 | ondemand_dryrun_script | `mas_ondemand.py --script 明日方舟-官-1-1-7 --json` | 2 | 2.2 |
| 10 | ondemand_list_running | `mas_ondemand.py --list-running --json` | 0 | 2.3 |
| 11 | ondemand_mcp_check | `mas_ondemand_mcp.py --check` | 0 | 10.4 |
| 12 | ondemand_wake_dry | `mas_ondemand.py --wake 明日方舟-官-1-1-7 --json` | 2 | 2.4 |
| 13 | plan_builder_dryrun | `mas_plan_builder.py --account y9Flipped --json --out-dir D:\1-2\MAAOrch\docs\audits\2026-09-28_tooling_audit\pb_out` | 0 | 1.3 |
| 14 | plan_builder_list_accounts | `mas_plan_builder.py --list-accounts` | 0 | 0.6 |
| 15 | probe_farm_dryrun | `mas_probe_farm.py --account y9Flipped --stage 1-7 --times 2` | 0 | 3.9 |
| 16 | probe_farm_dryrun_json | `mas_probe_farm.py --account y9Flipped --stage 1-7 --times 2 --json` | 0 | 8.5 |
| 17 | probe_farm_list_unknown | `mas_probe_farm.py --list unknown` | 0 | 3.4 |
| 18 | run_profile_docclaim_dryrun_flag | `mas_run_profile.py --profile runtime/templates/run_profile.example.json --dry-run` | 2 | 0.6 |
| 19 | run_profile_dryrun | `mas_run_profile.py --profile runtime/templates/run_profile.example.json` | 0 | 1.4 |
| 20 | run_profile_dryrun_json | `mas_run_profile.py --profile runtime/templates/run_profile.example.json --json` | 0 | 2.1 |
| 21 | run_profile_list_plans | `mas_run_profile.py --list-plans` | 0 | 1.7 |
| 22 | run_profile_reconcile_dry | `mas_run_profile.py --reconcile --json` | 0 | 0.7 |
| 23 | run_profile_restore_run_bogus | `mas_run_profile.py --restore-run rp_nonexistent --json` | 2 | 0.6 |
| 24 | run_profile_status | `mas_run_profile.py --status --json` | 0 | 1.1 |
| 25 | selftest_ondemand | `mas_ondemand.py --selftest` | 0 | 1.7 |
| 26 | selftest_ondemand_mcp | `mas_ondemand_mcp.py --selftest` | 0 | 16.2 |
| 27 | selftest_plan_builder | `mas_plan_builder.py --selftest` | 0 | 1.3 |
| 28 | selftest_probe_farm | `mas_probe_farm.py --selftest` | 0 | 0.8 |
| 29 | selftest_run_profile | `mas_run_profile.py --selftest` | 0 | 24.7 |
| 30 | selftest_stage_capability | `stage_capability.py --selftest` | 0 | 0.6 |
| 31 | stagecap_account | `stage_capability.py --account y9Flipped --json` | 0 | 0.5 |
| 32 | stagecap_check | `stage_capability.py --check` | 0 | 7.7 |
| 33 | stagecap_rebuild_local_nowrite | `stage_capability.py --rebuild-local --no-write` | 0 | 0.8 |
