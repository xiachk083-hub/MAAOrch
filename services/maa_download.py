"""Automatic MAA download, extract, and initialization on first run."""
from __future__ import annotations
import json, os, re, shutil, time, threading, urllib.request, zipfile, io
from pathlib import Path
from typing import Any

_CHUNK_SIZE = 64 * 1024  # 64KB download chunks
_RELEASE_API = "https://api.github.com/repos/MaaAssistantArknights/MaaRelease/releases/latest"
_RELEASE_PAGE = "https://github.com/MaaAssistantArknights/MaaRelease/releases/latest"
_USER_AGENT = "MAAOrch"

# Global lock: ensure_maa_available may be called from the startup background
# thread AND the /api/maa/download_update endpoint concurrently. Without this,
# two threads can rmtree()/rebuild maa/source at the same time, corrupting it.
_MAA_LOCK = threading.Lock()

# Download progress state — queried by /api/maa/download_status for the UI.
# {state: idle|downloading|extracting|init|done|error, pct, downloaded, total, version, error, message}
_dl_status: dict = {"state": "idle", "pct": 0, "downloaded": 0, "total": 0,
                    "version": "", "error": "", "message": ""}
_dl_status_lock = threading.Lock()


def _dl_update(**kw) -> None:
    with _dl_status_lock:
        _dl_status.update(kw)


def get_download_status() -> dict:
    with _dl_status_lock:
        return dict(_dl_status)


def _is_source_ready(source_dir: Path) -> bool:
    """Quick check: source MAA exists and has config.
    ⚠️ 不检查 $type — $type 是 MAAOrch 注入/初始化时生成的（MAA 6.16 保存
    配置后 TaskQueue 序列化为 {"type":...} 无 $type — 把 $type 当 ready
    条件会误判不 ready → 清 source 重下 → GitHub 不可达时 source 被清空
    （2026-08-12 部署放回后 source 丢失根因）。"""
    exe = source_dir / "MAA.exe"
    if not exe.exists():
        return False
    gj = source_dir / "config" / "gui.new.json"
    if not gj.exists():
        return False
    return True


def _detect_version(source_dir: Path, fallback_tag: str = "") -> str:
    """Extract MAA version from source directory or fallback."""
    if fallback_tag:
        return fallback_tag.lstrip("v")
    # Try reading version from MAA.exe's file metadata or directory name
    exe = source_dir / "MAA.exe"
    if exe.exists():
        try:
            import win32api  # optional, not always available
            info = win32api.GetFileVersionInfo(str(exe), "\\")
            ver = f"{info['FileVersionMS']>>16}.{info['FileVersionMS']&0xFFFF}." \
                  f"{info['FileVersionLS']>>16}.{info['FileVersionLS']&0xFFFF}"
            return ver.rstrip(".0")
        except Exception:
            pass
        # Fallback: check PE header timestamp or just return unknown
    return "unknown"


def _get_download_url() -> tuple[str, str] | None:
    """Get latest MAA win-x64 zip download URL from GitHub.
    Returns (url, tag_name) or None on failure.
    Falls back to HTML page parsing and GitHub mirrors — direct GitHub API is
    often blocked/rate-limited on some networks."""
    # Try direct API, direct HTML page, then mirror-proxied API/page.
    _MIRROR_PREFIXES = ["", "https://ghfast.top/", "https://ghproxy.net/", "https://gh-proxy.com/"]
    for prefix in _MIRROR_PREFIXES:
        for url, use_api in [
            (prefix + _RELEASE_API, True),
            (prefix + _RELEASE_PAGE, False),
        ]:
            try:
                req = urllib.request.Request(url, headers={"User-Agent": _USER_AGENT})
                if use_api:
                    req.add_header("Accept", "application/json")
                resp = safe_urlopen(req, timeout=15)
                if use_api:
                    data = json.loads(resp.read().decode())
                    tag = data.get("tag_name", "").lstrip("v")
                    for a in data.get("assets", []):
                        name = a.get("name", "")
                        if "win-x64" in name and name.endswith(".zip"):
                            return (a["browser_download_url"], tag)
                    continue
                else:
                    # HTML page: version in redirect URL or page title
                    final_url = resp.url
                    m = re.search(r'/tag/v?([\d.]+(?:-[\w.]+)?)', final_url)
                    tag = m.group(1).lstrip("v") if m else ""
                    if not tag:
                        html = resp.read().decode("utf-8", errors="replace")
                        m = re.search(r'MAA[\s-]*v?([\d.]+(?:-[\w.]+)?)', html)
                        tag = m.group(1).lstrip("v") if m else ""
                    # Parse HTML to find win-x64 download link
                    m = re.search(r'href="([^"]*win-x64[^"]*\.zip)"', html)
                    if m:
                        href = m.group(1)
                        dl_url = href if href.startswith("http") else f"https://github.com{href}"
                        return (dl_url, tag)
            except Exception:
                continue
    return None


def _download_zip(url: str, dest: Path, log, version: str = "") -> bool:
    """Download zip file to disk in chunks (avoids loading 200MB into RAM).
    Uses per-chunk read timeout (not just connect timeout) and retries 3x on stalls.
    Falls back to GitHub mirrors (ghfast.top etc.) — direct GitHub is often
    slow/blocked on the target machine. Returns True on success."""
    import socket
    _dl_update(state="downloading", version=version, error="", message="")
    # Read timeout: urllib's `timeout` only covers connect; a stalled read would
    # block forever. Setting the socket timeout covers each read() call.
    old_to = socket.getdefaulttimeout()
    socket.setdefaulttimeout(60)
    mirrors = [
        url,
        "https://ghfast.top/" + url,
        "https://ghproxy.net/" + url,
        "https://gh-proxy.com/" + url,
    ]
    try:
        for idx, cand in enumerate(mirrors):
            for attempt in range(1, 4):
                try:
                    if idx > 0:
                        log(f"  尝试镜像: {cand.split('/')[2]}")
                    req = urllib.request.Request(cand, headers={"User-Agent": _USER_AGENT})
                    resp = safe_urlopen(req, timeout=60)
                    total = int(resp.headers.get("Content-Length", 0))
                    downloaded = 0
                    last_log = 0
                    last_pct_log = 0
                    with open(dest, "wb") as f:
                        while True:
                            chunk = resp.read(_CHUNK_SIZE)
                            if not chunk:
                                break
                            f.write(chunk)
                            downloaded += len(chunk)
                            # Percentage-based logging when server reports Content-Length
                            if total and downloaded - last_pct_log > total // 10:
                                pct = downloaded * 100 // total
                                log(f"  下载中: {pct}% ({downloaded // 1048576}MB / {total // 1048576}MB)")
                                _dl_update(pct=pct, downloaded=downloaded, total=total)
                                last_pct_log = downloaded
                            # Byte-based fallback when Content-Length missing (GitHub often omits it)
                            elif not total and downloaded - last_log >= 8 * 1048576:
                                log(f"  下载中: {downloaded // 1048576}MB...")
                                _dl_update(downloaded=downloaded, total=0)
                                last_log = downloaded
                    log(f"  完成: {downloaded // 1048576}MB")
                    _dl_update(state="done", pct=100, downloaded=downloaded, total=total or downloaded)
                    return True
                except Exception as e:
                    log(f"  第 {attempt} 次下载中断: {e}")
                    if attempt < 3:
                        log(f"  重试中 ({attempt}/3)...")
                        _dl_update(message=f"下载中断，重试 {attempt}/3: {e}")
                        time.sleep(3)
                    else:
                        _dl_update(state="error", error=str(e), message=f"下载失败: {e}")
                        break
    finally:
        socket.setdefaulttimeout(old_to)
    return False


def _extract_zip(zip_path: Path, source_dir: Path, log) -> bool:
    """Extract zip to source_dir, handling possible top-level directory nesting.
    File-by-file with progress logging + per-file fault tolerance — extractall()
    on 9000+ files gave zero progress and hung silently under AV scanning."""
    log("解压中...")
    if source_dir.exists():
        for _ in range(5):
            try:
                shutil.rmtree(source_dir)
                break
            except Exception:
                time.sleep(2)
    source_dir.mkdir(parents=True, exist_ok=True)
    try:
        with zipfile.ZipFile(zip_path) as zf:
            members = zf.namelist()
            top_dirs = set()
            for m in members:
                parts = m.split("/")
                if len(parts) > 1 and parts[0]:
                    top_dirs.add(parts[0])
            has_top = len(top_dirs) == 1 and all(
                m.startswith(list(top_dirs)[0] + "/") or m == list(top_dirs)[0] or m == list(top_dirs)[0] + "/"
                for m in members if m
            )
            top = list(top_dirs)[0] if has_top and list(top_dirs)[0] else ""
            total = len(members)
            done = 0
            for m in members:
                if m == top or m == top + "/":
                    continue
                rel = m[len(top) + 1:] if top and m.startswith(top + "/") else m
                if not rel:
                    continue
                dest = source_dir / rel
                try:
                    if m.endswith("/"):
                        dest.mkdir(parents=True, exist_ok=True)
                    else:
                        dest.parent.mkdir(parents=True, exist_ok=True)
                        with zf.open(m) as src, open(dest, "wb") as dst:
                            dst.write(src.read())
                except Exception as e:
                    log(f"  跳过文件(损坏): {m}: {e}")
                done += 1
                if done % 1000 == 0:
                    log(f"  解压进度: {done}/{total}")
                    _dl_update(message=f"解压 {done}/{total}")
        for f in source_dir.rglob("*"):
            try:
                os.chmod(f, 0o755)
            except Exception:
                pass
        log("  解压完成")
        return True
    except Exception as e:
        log(f"解压失败: {e}")
        return False


def _init_maa_source_wrapper(source_dir: Path, log) -> bool:
    """Run MAA.exe briefly to generate $type config fields."""
    from services.instance_pool import _init_maa_source
    log("初始化 MAA 配置（运行 MAA.exe 生成 $type）...")
    ok = _init_maa_source(source_dir)
    if ok:
        log("  MAA 初始化完成")
    else:
        log("  MAA 初始化未完全完成（$type 未生成），部分功能可能受限")
    return ok


def ensure_maa_available(ctx: Any, source_dir: Path) -> bool:
    """Ensure MAA is available in source_dir. Auto-download if missing.
    Returns True if MAA is usable after the attempt.
    Thread-safe: concurrent calls (startup bg thread + manual update) serialize."""
    with _MAA_LOCK:
        return _ensure_maa_available_locked(ctx, source_dir)


def _ensure_maa_available_locked(ctx: Any, source_dir: Path) -> bool:
    """Internal implementation — must be called with _MAA_LOCK held."""
    _log = ctx.log

    # Step 0: Already ready? (re-check under lock — another thread may have finished)
    if _is_source_ready(source_dir):
        ver = _detect_version(source_dir)
        if ver != "unknown":
            ctx.config["maa_version"] = ver
        _dl_update(state="done", pct=100, version=ver, message="就绪")
        return True

    # If source exists but MAA.exe missing, it's corrupted → clean up
    if source_dir.exists():
        exe = source_dir / "MAA.exe"
        if not exe.exists():
            _log("[MAA] source 目录不完整，清理后重新下载")
            # Orphaned MAA.exe processes hold locks on source files and make
            # rmtree fail → mkdir then hits WinError 183. Kill them first.
            try:
                import subprocess as _sp
                _sp.run(["taskkill", "/F", "/IM", "MAA.exe"],
                        capture_output=True, timeout=10,
                        creationflags=_sp.CREATE_NO_WINDOW)
            except Exception:
                pass
            time.sleep(2)
            for _ in range(5):
                try:
                    shutil.rmtree(source_dir)
                    break
                except Exception:
                    time.sleep(2)
            source_dir.mkdir(parents=True, exist_ok=True)
        else:
            # MAA.exe present but config not initialized (fresh official zip has
            # no config/) — run MAA once to generate $type instead of re-downloading
            # the whole 250MB package (previous behavior looped forever).
            gj = source_dir / "config" / "gui.new.json"
            _need_init = not gj.exists()
            if gj.exists():
                # config 存在但 $type 缺失（MAA 保存后序列化丢失）→ 重新
                # 初始化补 $type — 防"判不 ready → 清 source 重下"循环
                try:
                    d = json.loads(gj.read_text(encoding="utf-8"))
                    tq = d.get("Configurations", {}).get("Default", {}).get("TaskQueue", [])
                    if tq and not any("$type" in item for item in tq):
                        _need_init = True
                        _log("[MAA] gui.new.json 缺 $type（MAA 保存后序列化丢失），重新初始化")
                except Exception:
                    pass
            if _need_init:
                _log("[MAA] MAA.exe 存在但配置未初始化，尝试初始化...")
                try:
                    _init_maa_source_wrapper(source_dir, _log)
                except Exception as e:
                    _log(f"[MAA] 初始化失败: {e}")
                if _is_source_ready(source_dir):
                    ver = _detect_version(source_dir)
                    if ver != "unknown":
                        ctx.config["maa_version"] = ver
                    _dl_update(state="done", pct=100, version=ver, message="就绪(初始化完成)")
                    return True

    # Step 1: Get download URL
    _log("[MAA] 检查 GitHub 最新版本...")
    result = _get_download_url()
    if not result:
        _log("[MAA] 无法获取下载链接（网络问题或 GitHub 限频）")
        _log("[MAA] 手动修复: 1) 浏览器打开 https://github.com/MaaAssistantArknights/MaaAssistantArknights/releases")
        _log("[MAA]          2) 下载 win-x64.zip 解压到 services/maa/source/（MAA.exe 在根目录）")
        _log("[MAA]          3) 重启 MAAOrch 完成初始化")
        source_dir.mkdir(parents=True, exist_ok=True)
        return False
    dl_url, tag = result
    _log(f"[MAA] 发现新版: {tag}")
    _dl_update(state="downloading", version=tag, pct=0, downloaded=0, total=0, error="", message="")

    # Step 2: Download to temp file
    source_dir.mkdir(parents=True, exist_ok=True)
    temp_zip = source_dir.parent / f"_download_{int(time.time())}.zip"
    if not _download_zip(dl_url, temp_zip, _log, version=tag):
        temp_zip.unlink(missing_ok=True)
        return False

    # Step 3: Extract
    _dl_update(state="extracting", message="解压中...")
    if not _extract_zip(temp_zip, source_dir, _log):
        temp_zip.unlink(missing_ok=True)
        _dl_update(state="error", error="解压失败", message="解压失败")
        return False
    temp_zip.unlink(missing_ok=True)

    # Step 4: Init MAA (generate $type)
    _dl_update(state="init", message="初始化 MAA 配置...")
    _init_maa_source_wrapper(source_dir, _log)

    # Step 5: Record version
    version = _detect_version(source_dir, tag)
    ctx.config["maa_version"] = version
    ctx.config.setdefault("maa_instances_version", "")
    # Trigger instance rebuild on next ensure_maa_instances call
    ctx.config["maa_instances_version"] = ""
    try:
        from models.config_manager import save_config
        save_config(ctx.config)
    except Exception:
        pass

    if _is_source_ready(source_dir):
        _log(f"[MAA] 就绪 (版本 {version})")
        _dl_update(state="done", pct=100, version=version, message="就绪")
        return True
    else:
        _log("[MAA] MAA 已下载但初始化不完全，请手动运行一次 MAA.exe")
        _dl_update(state="error", version=version, message="初始化不完全")
        return False
