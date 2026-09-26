"""账号 CRUD + 分组管理 + 日志查询。数据存 data/accounts.json。"""
from __future__ import annotations
import json
import shutil
import threading
from datetime import datetime
from pathlib import Path

from fastapi import APIRouter, HTTPException

from .config import data_file
from .oplog import log as oplog

router = APIRouter()
_LOCK = threading.Lock()
ACCOUNTS_FILE = data_file("accounts.json")


def _ensure_accounts_file() -> None:
    ACCOUNTS_FILE.parent.mkdir(parents=True, exist_ok=True)
    if not ACCOUNTS_FILE.exists():
        ACCOUNTS_FILE.write_text(json.dumps({"groups": [], "accounts": []},
                                            ensure_ascii=False, indent=2),
                                 encoding="utf-8")


def _load() -> dict:
    _ensure_accounts_file()
    try:
        return json.loads(ACCOUNTS_FILE.read_text(encoding="utf-8"))
    except Exception:
        return {"groups": [], "accounts": []}


def _save(data: dict) -> None:
    ACCOUNTS_FILE.write_text(json.dumps(data, ensure_ascii=False, indent=2),
                             encoding="utf-8")


def _next_id(accounts: list) -> str:
    used = {str(a.get("id", "")) for a in accounts}
    n = 1
    while f"账号{n}" in used:
        n += 1
    return f"账号{n}"


def list_accounts() -> dict:
    return _load()


def get_account(acc_id: str) -> dict | None:
    return next((a for a in _load()["accounts"] if a.get("id") == acc_id), None)


# ---------- 分组 ----------

@router.get("/api/groups")
def api_groups():
    return {"ok": True, "groups": _load().get("groups", [])}


@router.post("/api/groups")
def api_create_group(body: dict):
    name = str(body.get("name", "")).strip()
    if not name:
        raise HTTPException(status_code=400, detail="组名不能为空")
    with _LOCK:
        data = _load()
        if name in data["groups"]:
            raise HTTPException(status_code=400, detail=f"组已存在: {name}")
        data["groups"].append(name)
        _save(data)
    oplog("新建分组", name, "ok")
    return {"ok": True}


@router.put("/api/groups/{name}")
def api_rename_group(name: str, body: dict):
    new_name = str(body.get("name", "")).strip()
    if not new_name:
        raise HTTPException(status_code=400, detail="组名不能为空")
    with _LOCK:
        data = _load()
        if name not in data["groups"]:
            raise HTTPException(status_code=404, detail=f"组不存在: {name}")
        if new_name != name and new_name in data["groups"]:
            raise HTTPException(status_code=400, detail=f"组已存在: {new_name}")
        data["groups"] = [new_name if g == name else g for g in data["groups"]]
        for a in data["accounts"]:
            if a.get("group") == name:
                a["group"] = new_name
        _save(data)
    oplog("重命名分组", f"{name}→{new_name}", "ok")
    return {"ok": True}


@router.delete("/api/groups/{name}")
def api_delete_group(name: str):
    with _LOCK:
        data = _load()
        if name not in data["groups"]:
            raise HTTPException(status_code=404, detail=f"组不存在: {name}")
        data["groups"].remove(name)
        for a in data["accounts"]:
            if a.get("group") == name:
                a["group"] = ""  # 回未分组
        _save(data)
    oplog("删除分组", name, "ok")
    return {"ok": True}


# ---------- 账号 ----------

@router.get("/api/accounts")
def api_accounts():
    return {"ok": True, **_load()}


@router.post("/api/accounts")
def api_create_account(body: dict):
    name = str(body.get("name", "")).strip()
    emu_index = str(body.get("emu_index", "") or "")
    group = str(body.get("group", "") or "")
    note = str(body.get("note", "") or "")
    if not name:
        raise HTTPException(status_code=400, detail="账号名不能为空")
    with _LOCK:
        data = _load()
        acc = {
            "id": _next_id(data["accounts"]),
            "name": name,
            "emu_index": emu_index,
            "group": group if group in data["groups"] else "",
            "note": note,
        }
        data["accounts"].append(acc)
        _save(data)
    oplog("新建账号", name, "ok", f"模拟器#{emu_index}")
    return {"ok": True, "account": acc}


@router.post("/api/accounts/{acc_id}/note")
def api_account_note(acc_id: str, body: dict):
    note = str(body.get("note", "") or "")
    with _LOCK:
        data = _load()
        acc = next((a for a in data["accounts"] if a.get("id") == acc_id), None)
        if not acc:
            raise HTTPException(status_code=404, detail=f"账号不存在: {acc_id}")
        acc["note"] = note
        _save(data)
    oplog("改备注", acc_id, "ok")
    return {"ok": True}


@router.post("/api/accounts/{acc_id}/bind_emu")
def api_account_bind_emu(acc_id: str, body: dict):
    emu_index = str(body.get("emu_index", "") or "")
    with _LOCK:
        data = _load()
        acc = next((a for a in data["accounts"] if a.get("id") == acc_id), None)
        if not acc:
            raise HTTPException(status_code=404, detail=f"账号不存在: {acc_id}")
        acc["emu_index"] = emu_index
        _save(data)
    oplog("绑定模拟器", acc_id, "ok", f"模拟器#{emu_index}")
    return {"ok": True}


@router.post("/api/accounts/{acc_id}/group")
def api_account_group(acc_id: str, body: dict):
    group = str(body.get("group", "") or "")
    with _LOCK:
        data = _load()
        acc = next((a for a in data["accounts"] if a.get("id") == acc_id), None)
        if not acc:
            raise HTTPException(status_code=404, detail=f"账号不存在: {acc_id}")
        if group and group not in data["groups"]:
            raise HTTPException(status_code=400, detail=f"组不存在: {group}")
        acc["group"] = group
        _save(data)
    oplog("账号移组", acc_id, "ok", group or "未分组")
    return {"ok": True}


@router.delete("/api/accounts/{acc_id}")
def api_delete_account(acc_id: str):
    with _LOCK:
        data = _load()
        before = len(data["accounts"])
        data["accounts"] = [a for a in data["accounts"] if a.get("id") != acc_id]
        if len(data["accounts"]) == before:
            raise HTTPException(status_code=404, detail=f"账号不存在: {acc_id}")
        _save(data)
    # 删配置存档 + 日志目录
    for p in (data_file("configs", f"{acc_id}.json"),
              data_file("logs", acc_id)):
        try:
            if p.is_dir():
                shutil.rmtree(p)
            elif p.exists():
                p.unlink()
        except Exception:
            pass
    oplog("删除账号", acc_id, "ok")
    return {"ok": True}


# ---------- 日志查询 ----------

@router.get("/api/accounts/{acc_id}/logs")
def api_account_logs(acc_id: str):
    """该账号日志文件夹列表（按时间）。"""
    logs_dir = data_file("logs", acc_id)
    runs = []
    if logs_dir.is_dir():
        for d in sorted(logs_dir.iterdir()):
            if d.is_dir():
                files = [f.name for f in d.iterdir() if f.is_file()]
                runs.append({"time": d.name, "files": files})
    return {"ok": True, "runs": runs}


@router.get("/api/accounts/{acc_id}/logs/{run}")
def api_account_log_content(acc_id: str, run: str):
    """某次运行的日志内容（全部文件合并，按文件名分段）。"""
    run_dir = data_file("logs", acc_id, run)
    if not run_dir.is_dir():
        raise HTTPException(status_code=404, detail="日志不存在")
    out = []
    for f in sorted(run_dir.iterdir()):
        if f.is_file() and f.suffix.lower() in (".log", ".txt", ".json"):
            try:
                content = f.read_text(encoding="utf-8", errors="replace")[-200000:]
            except Exception:
                content = "(读取失败)"
            out.append({"file": f.name, "content": content})
    return {"ok": True, "run": run, "files": out}


def rename_account_dir(old_id: str, new_id: str) -> None:
    """账号 id 变更时迁移 configs/logs 目录（预留）。"""
    for p in (data_file("configs", f"{old_id}.json"), data_file("logs", old_id)):
        try:
            if p.exists():
                dst = data_file("configs", f"{new_id}.json") if p.suffix else data_file("logs", new_id)
                shutil.move(str(p), str(dst))
        except Exception:
            pass


# ---------- 配置存档 ----------

def load_account_config(acc_id: str) -> dict | None:
    """读取账号的 MAA 配置存档（完整 gui.new.json 内容）。"""
    p = data_file("configs", f"{acc_id}.json")
    if not p.exists():
        return None
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except Exception:
        return None


def save_account_config(acc_id: str, cfg: dict) -> None:
    p = data_file("configs", f"{acc_id}.json")
    p.parent.mkdir(parents=True, exist_ok=True)
    tmp = p.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(cfg, ensure_ascii=False, indent=2), encoding="utf-8")
    tmp.replace(p)
