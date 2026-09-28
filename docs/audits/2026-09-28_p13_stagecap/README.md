# P1-3 / P2-8 冒烟证据：`tools/stage_capability.py` 原子写 + 单飞锁

卡片：`t_a69d0027`。目标：修 `docs/TOOLING_AUDIT.md` 的 **P1-3**（写盘非原子、读者可能读到半截 JSON）
与 **P2-8**（并发读-改-写丢更新），并留下可复核的冒烟证据。**只动一个工具文件**
（`tools/stage_capability.py`，sha256 见尾注）；数据源保持只读。

## 改了什么（代码面，一段话）

- **原子写层** `_write_text_atomic(path, text)`：`<name>.tmp.<pid>` 写好再 `os.replace` 换名。
  三个产物（state / 报告 JSON / 报告 MD）与 `--rebuild-local` 全部改走它；读者永远只见
  「旧文 or 新文」。tmp 带 pid ⇒ 并发写者互不截断；进程被杀最多留一个孤立 tmp（无害）。
- **读侧配对重试** `_load_json(path)`：Windows 上 `replace` 的瞬间，读者开文件可能吃到
  `ERROR_ACCESS_DENIED/ENOENT`（下方实测），读侧 ~150ms 内重试骑过；耗尽后照常抛错（不吞）。
- **单飞锁** `_stage_lock()`：`runtime/stage_capability.lock`（O_EXCL + 陈旧抢占 + 释放清理，
  协议同 `mas_dispatch._dispatch_lock`）。`_finish` 的 update 读-改-写**整段持锁**，
  `--rebuild-local` 的「读 state→写报表」也持同一把锁（否则盘上报表可能落后于 state）。
  锁被占先等（`MAAORCH_STAGECAP_LOCK_WAIT_S`，默认 360s），超时**明确让路 rc=2**；等/获锁都留痕。
- **审计**：写盘后补一条 `mas_audit`（`tool:stage_capability` / `stage.rebuild`，含
  mode/state_attempts/rows/scanned/added/updated/dropped + 三产物路径）；审计失败不影响主流程。
- **契约写明**：本工具**默认真写盘**（与其余五工具默认 dry-run 相反），干跑加 `--no-write`；
  `--selftest` 增加 6 条写盘层用例；`--help`/docstring 同步。

## 证据清单（本目录，全部为本机实跑输出）

| 文件 | 证明 |
|---|---|
| `out_selftest.txt` | `--selftest` 全绿：9 条分类器 + **6 条写盘层**（原子写覆盖/锁忙让路/陈旧抢占/读-改-写回环/干跑不碰盘/rebuild-local 同锁） |
| `out_smoke_concurrency.txt` | 并发冒烟 **SMOKE=PASS**：phase1b 两进程各 50000 次 update 并入（共 100000，不丢更新）；phase2 两个**真实** `--rebuild-local` 并发 rc=0 |
| `out_phase1_workerA/B.txt`、`out_phase1b_workerA/B.txt`、`out_phase2_rebuild_a/b.txt` | 各 worker 完整 stdout：等待/获锁痕、写出路径、rc；phase2-a 实证「写盘锁被占（pid=…），等待中」 |
| `out_race_naive_raw.txt` | 旧写法（直写）+ 紧循环读者：**torn=312**（读者真读到半截 JSON） |
| `out_race_atomic_raw.txt` | 新写法 + 紧循环读者：**torn=0**；但病态读者（几乎 100% 占着句柄）会把写者饿死（见「已知边界」） |
| `out_race_atomic_retryreader.txt` | 新写法 + 工具内读法 `_load_json`：**torn=0、读侧错误=0**（重试把替换瞬间骑过去） |
| `out_race_naive_realread.txt` | 旧写法 + 现实读者（5ms 间隔）：**torn=28**（半截真的会被读到） |
| `out_race_atomic_realread.txt` | 新写法 + 现实读者：**torn=0**（读侧 14 次瞬时 ACCESS_DENIED，被 `_load_json` 吸收）；写者 300/300 |
| `out_kill_naive.txt` | 手工中断冒烟（SIGKILL 写者）×3：旧写法 `final_ok=0` ⇒ 盘上留下坏文件 |
| `out_kill_atomic.txt` | 手工中断冒烟 ×3：新写法 `final_ok=1`（文件永远可解析；剩 1-2 个孤立 `.tmp.<pid>`，无害） |
| `out_verify_outputs.txt` | **VERIFY=PASS**：state 读入→新代码重写**字节一致**；报告 JSON/MD 重渲染与盘上一致；`can_farm` 样本与台账一致、台账外账号→`unknown`；审计流有 `stage.rebuild` 行 |
| `out_cli_rebuild_dry.txt` + `_mt_before/_mt_after.txt` | `--rebuild-local --no-write` 干跑：三产物 sha256 **前后不变**（真·不碰盘） |
| `out_cli_account.txt`、`out_cli_account_json.txt`、`out_cli_json.txt`、`out_cli_help.txt`、`out_cli_check.txt` | 读路径输出样本（`--account` / `--json` / `--help` / `--check` OK），供回归对照 |
| `out_regression_offline.txt` | `tools/tests/test_regression.py`：**REGRESSION=PASS (29 passed, 0 failed, 5 skipped)** |
| `p13_race.py`、`p13_smoke.py`、`p13_verify_outputs.py` | 三个探针/冒烟脚本本体（可重跑，见下） |

## 复现命令（Python312 全路径）

```bash
W=C:/Users/xiach/AppData/Local/Programs/Python/Python312/python.exe
"$W" tools/stage_capability.py --selftest
"$W" tools/tests/test_regression.py
"$W" docs/audits/2026-09-28_p13_stagecap/p13_race.py --mode naive  --iters 400                                  # 旧写法 torn>0
"$W" docs/audits/2026-09-28_p13_stagecap/p13_race.py --mode atomic --iters 400 --reader-gap 0.005               # 新写法 torn=0
"$W" docs/audits/2026-09-28_p13_stagecap/p13_race.py --mode atomic --iters 200 --kill-after 1.5 --reader-gap 0.002   # 中断冒烟 final_ok=1
"$W" docs/audits/2026-09-28_p13_stagecap/p13_smoke.py --sandbox <绝对沙箱目录> --count 50000 --evidence docs/audits/2026-09-28_p13_stagecap
"$W" docs/audits/2026-09-28_p13_stagecap/p13_verify_outputs.py
```

## Windows 实测结论（写给后人）

1. `os.replace` 换名目标时，**读者的读句柄会让换名撞共享锁**（`PermissionError [WinError 5]`）：
   写者因此带 ~1s 有界重试（10×100ms）。现实读者是「开→读→关」毫秒级，重试足够骑过。
2. 反向也有窗口：`replace` 的瞬间，读者开文件可能吃到瞬时 `ACCESS_DENIED`（实测紧循环读者
   ~1/100 次命中）。工具内读者走 `_load_json` 重试吸收；**外部工具读者**（`mas_probe_farm`
   的 ledger_lookup、`mas_plan_builder`）仍是裸 `open`，撞上会拿到异常——但那需要恰好撞上
   亚毫秒窗口，且旧写法在同样场景下会读到**半截 JSON**（更糟）。见 `out_race_atomic_realread.txt`。
3. **已知边界（不修，写在这）**：一个几乎 100% 时间占着目标读句柄的「病态读者」会把写者饿死
   （重试耗尽 → `_write_text_atomic` 抛错、`_finish` rc=1）。这是 Win32 换名语义的硬边界，
   不是本工具能单边解决的（需要读者侧开 `FILE_SHARE_DELETE`）；真实仓库读者不存在这种形态。
4. 文本模式写入在 Windows 上落盘是 CRLF（**旧写法同样如此**）；比对产物时按文本读回（LF）
   再比，勿按原始字节比对渲染字符串。state 的读入→重写字节一致已在 `out_verify_outputs.txt` 里验过。

## 旁证：真实并发消费者当天就压过一次

2026-09-28 10:27:03，`mas_probe_farm` 流程（`pf_20260928_100849`）实测跑了**真实**
`stage_capability.py --update`（rc=0，scanned=148、added=16），随后读回台账记账
`unknown → success`（`runtime/mas_probe_farm_log.jsonl`）。即：改完的写盘/锁/审计链路被
真实消费者走通，非仅沙箱自证。

另：真实 `--rebuild-local`（11:05:35）在真审计流留下第 3 条 `stage.rebuild` 行
（`{"mode": "rebuild-local", "state_attempts": 5097, "rows": 288}`）；写盘锁此时不残留
（`runtime/*.lock` 无文件）。

## 影响面

- 改动文件：`tools/stage_capability.py`（唯一生产文件；sha256 `d20fec5c…fa161c`，1321 行）。
- 新增（本目录）：3 个脚本 + 上述 .txt 证据。
- 未动：数据源、其余工具、`tools/tests/test_regression.py`（注意：当天另有并行工作者在改它，
  32→33 用例，非本卡片所为；用其最新版跑仍是 0 failed）。
