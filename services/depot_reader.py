"""Depot reader — MAA 官方仓库识别（Depot 任务），子进程模式。

用法（CLI）:
    python services/depot_reader.py --adb 127.0.0.1:16384 --account 官-0

用法（产品化，depot_scheduler 调用）:
    reader = DepotReader(addr, account_id="xxx", client_type="Official")
    snap = reader.read()   # 快照落盘 models/accounts/<id>/depot.json

识别协议（docs.maa.plus callback-schema 实测 2026-08-13）:
    append_task('Depot', {'enable': True}) → 回调 SubTaskExtraInfo(20003)
    what="DepotInfo"(文档写 Depot, 实测 DepotInfo, 前缀匹配兼容)
    details.done=true 时 data 是 {"物品ID": 数量} JSON 字符串。
    item_index.json 映射 ID → 材料名。

子进程模式复刻 stage_probe._run_asst — MaaCore in-process 会崩项目进程
(2026-08-12 probe 实测), 崩了只影响子进程。
"""
from __future__ import annotations
import json
import re
import sys
import time
import pathlib
import subprocess

sys.path.insert(0, str(pathlib.Path(__file__).parent.parent))  # 单独运行时可 import infrastructure

ROOT = pathlib.Path(__file__).parent.parent
# AsstMsg 枚举数值 (docs.maa.plus callback-schema, MAA v6): SubTaskExtraInfo = 20003
SUBTASK_EXTRA_INFO = 20003


class DepotReader:
    """Read one account's warehouse via MAA Depot task (subprocess Asst)."""

    def __init__(self, adb_address: str, account: str = "",
                 account_id: str = "", client_type: str = "Official",
                 maa_instance: int = 1, timeout: float = 420.0) -> None:
        self.adb_address = adb_address
        self.account = account            # 展示名（快照标注）
        self.account_id = account_id      # 产品化落盘路径 models/accounts/<id>/
        self.client_type = client_type
        self.maa_instance = maa_instance
        self.timeout = timeout  # 冷启动游戏 + 全仓库翻页识别

    # ── helpers ──
    def _log(self, msg: str) -> None:
        print(f"[{time.strftime('%H:%M:%S')}] {msg}", flush=True)

    def _inst_dir(self) -> pathlib.Path:
        return (ROOT / "services" / "maa" / "instances" / str(self.maa_instance))

    def _adb_path(self) -> str:
        from infrastructure.task_constants import find_mumu_cli, find_adb
        cli = find_mumu_cli()
        if cli:
            cand = pathlib.Path(cli).parent / "adb.exe"
            if cand.exists():
                return str(cand)
        return find_adb() or "adb"

    # ── subprocess Asst ──
    def _run_asst(self) -> tuple[dict | None, str]:
        """Run StartUp + Depot in a subprocess; capture Depot result JSON."""
        inst_dir = str(self._inst_dir())
        py_dir = str(self._inst_dir() / "Python")
        lines = [
            "import sys, json, time",
            "sys.path.insert(0, %r)" % py_dir,
            "from asst.asst import Asst",
            "SUB_EXTRA = 20003",
            "captured = {}",
            "@Asst.CallBackType",
            "def _cb(msg, details, arg):",
            "    try:",
            "        d = json.loads(details.decode('utf-8', 'replace'))",
            "    except Exception:",
            "        return",
            "    if msg == SUB_EXTRA and str(d.get('what', '')).startswith('Depot'):",
            "        det = d.get('details') or {}",
            "        if det.get('done'):",
            "            try:",
            "                captured.update(json.loads(det.get('data') or '{}'))",
            "            except Exception:",
            "                pass",
            "Asst.load(path=%r)" % inst_dir,
            "asst = Asst(_cb)",
            "ok = asst.connect(%r, %r)" % (self._adb_path(), self.adb_address),
            "if not ok:",
            "    print('CONNECT_FAIL')",
            "    sys.exit(1)",
            "asst.append_task('StartUp', {'client_type': %r, 'start_game_enabled': True})" % self.client_type,
            "asst.append_task('Depot', {'enable': True})",
            "asst.start()",
            "dl = time.time() + %r" % self.timeout,
            "while time.time() < dl and asst.running():",
            "    time.sleep(2)",
            "asst.stop()",
            "print('DEPOT_RESULT ' + json.dumps(captured, ensure_ascii=False))",
        ]
        code = "\n".join(lines)
        try:
            r = subprocess.run([sys.executable, "-c", code],
                               capture_output=True, text=True,
                               timeout=self.timeout + 180,
                               encoding="utf-8", errors="replace",
                               creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        except subprocess.TimeoutExpired:
            return None, "子进程超时"
        out = r.stdout or ""
        if "CONNECT_FAIL" in out:
            return None, f"ADB 连接失败 ({self.adb_address})"
        m = re.search(r"DEPOT_RESULT (\{.*\})", out, re.S)
        if m:
            try:
                return json.loads(m.group(1)), ""
            except Exception:
                pass
        tail = (out or (r.stderr or "")).strip()[-200:]
        return None, f"识别无结果: {tail}"

    # ── name mapping ──
    def _load_names(self) -> dict[str, str]:
        p = self._inst_dir() / "resource" / "item_index.json"
        d = json.loads(p.read_text(encoding="utf-8"))
        return {k: v.get("name", k) for k, v in d.items()}

    def _map_names(self, raw: dict[str, int]) -> dict[str, int]:
        names = self._load_names()
        out: dict[str, int] = {}
        for item_id, count in raw.items():
            name = names.get(str(item_id), str(item_id))
            out[name] = out.get(name, 0) + int(count)
        return out

    # ── main flow ──
    def read(self) -> dict:
        """Run depot recognition (retry once); returns snapshot dict.

        account_id 给定时快照覆盖写 models/accounts/<id>/depot.json
        （最新一份，计划/趋势直接吃它）；否则按时间戳落 data/depot_snapshots/。
        """
        ts = time.strftime("%Y%m%d_%H%M%S")
        self._log(f"仓库识别 {self.account or self.adb_address} "
                  f"(client={self.client_type}, timeout={self.timeout:.0f}s)")
        raw, err = self._run_asst()
        if err or not raw:
            self._log(f"第一次识别失败: {err} — 5s 后重试")
            time.sleep(5)
            raw, err = self._run_asst()
        if err or not raw:
            self._log(f"识别失败: {err or '无识别结果'}")
            return {"account": self.account, "ts": ts, "error": err or "无识别结果"}
        named = self._map_names(raw)
        snap = {"account": self.account, "account_id": self.account_id, "ts": ts,
                "adb_address": self.adb_address,
                "client_type": self.client_type,
                "item_count": len(raw),
                "raw": {k: int(v) for k, v in raw.items()},
                "named": named}
        if self.account_id:
            d = ROOT / "models" / "accounts" / self.account_id
            d.mkdir(parents=True, exist_ok=True)
            out = d / "depot.json"
        else:
            d = ROOT / "data" / "depot_snapshots"
            d.mkdir(parents=True, exist_ok=True)
            tag = self.account or "emu"
            out = d / f"{tag}_{ts}.json"
        out.write_text(json.dumps(snap, ensure_ascii=False, indent=2),
                       encoding="utf-8")
        self._log(f"识别完成: {len(raw)} 种物品 → {out}")
        snap["_snapshot_file"] = str(out)
        return snap


def run_cli() -> None:
    import argparse
    ap = argparse.ArgumentParser(description="MAA Depot 仓库识别")
    ap.add_argument("--adb", required=True, help="adb 地址, 如 127.0.0.1:16384")
    ap.add_argument("--account", default="", help="账号标签(用于快照命名)")
    ap.add_argument("--client-type", default="Official", help="Official/Bilibili")
    ap.add_argument("--timeout", type=float, default=420.0)
    args = ap.parse_args()
    reader = DepotReader(args.adb, account=args.account,
                         client_type=args.client_type, timeout=args.timeout)
    snap = reader.read()
    if snap.get("error"):
        print(f"ERROR: {snap['error']}")
        sys.exit(1)
    print("=== TOP 20 ===")
    for name, count in sorted(snap["named"].items(),
                              key=lambda kv: -kv[1])[:20]:
        print(f"{name}: {count}")


if __name__ == "__main__":
    run_cli()
