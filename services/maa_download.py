"""Automatic MAA download, extract, and initialization on first run."""
from __future__ import annotations
import json, os, re, shutil, time, urllib.request, zipfile, io
from pathlib import Path
from typing import Any

_CHUNK_SIZE = 64 * 1024  # 64KB download chunks
_RELEASE_API = "https://api.github.com/repos/MaaAssistantArknights/MaaRelease/releases/latest"
_RELEASE_PAGE = "https://github.com/MaaAssistantArknights/MaaRelease/releases/latest"
_USER_AGENT = "MAAOrch"


def _is_source_ready(source_dir: Path) -> bool:
    """Quick check: source MAA exists and has $type config."""
    exe = source_dir / "MAA.exe"
    if not exe.exists():
        return False
    gj = source_dir / "config" / "gui.new.json"
    if not gj.exists():
        return False
    try:
        d = json.loads(gj.read_text(encoding="utf-8"))
        tq = d.get("Configurations", {}).get("Default", {}).get("TaskQueue", [])
        return any("$type" in item for item in tq)
    except Exception:
        return False


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
    Falls back to HTML page parsing when API is rate-limited or blocked."""
    # Try API first
    for url, use_api in [
        (_RELEASE_API, True),
        (_RELEASE_PAGE, False),
    ]:
        try:
            req = urllib.request.Request(url, headers={"User-Agent": _USER_AGENT})
            if use_api:
                req.add_header("Accept", "application/json")
            resp = urllib.request.urlopen(req, timeout=15)
            if use_api:
                data = json.loads(resp.read().decode())
                tag = data.get("tag_name", "").lstrip("v")
                for a in data.get("assets", []):
                    name = a.get("name", "")
                    if "win-x64" in name and name.endswith(".zip"):
                        return (a["browser_download_url"], tag)
                return None
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
                return None
        except Exception:
            continue
    return None


def _download_zip(url: str, dest: Path, log) -> bool:
    """Download zip file to disk in chunks (avoids loading 200MB into RAM).
    Returns True on success."""
    log(f"下载 MAA: {url.split('/')[-1]}")
    try:
        req = urllib.request.Request(url, headers={"User-Agent": _USER_AGENT})
        resp = urllib.request.urlopen(req, timeout=120)
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
                    last_pct_log = downloaded
                # Byte-based fallback when Content-Length missing (GitHub often omits it)
                elif not total and downloaded - last_log >= 8 * 1048576:
                    log(f"  下载中: {downloaded // 1048576}MB...")
                    last_log = downloaded
        log(f"  完成: {downloaded // 1048576}MB")
        return True
    except Exception as e:
        log(f"下载失败: {e}")
        dest.unlink(missing_ok=True)
        return False


def _extract_zip(zip_path: Path, source_dir: Path, log) -> bool:
    """Extract zip to source_dir, handling possible top-level directory nesting."""
    log("解压中...")
    if source_dir.exists():
        shutil.rmtree(source_dir, ignore_errors=True)
    source_dir.mkdir(parents=True)
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
            if has_top and list(top_dirs)[0]:
                top = list(top_dirs)[0]
                log(f"  检测到顶层目录: {top}")
                for m in members:
                    if m == top or m == top + "/":
                        continue
                    rel = m[len(top) + 1:] if m.startswith(top + "/") else m
                    if not rel:
                        continue
                    dest = source_dir / rel
                    if m.endswith("/"):
                        dest.mkdir(parents=True, exist_ok=True)
                    else:
                        dest.parent.mkdir(parents=True, exist_ok=True)
                        with zf.open(m) as src, open(dest, "wb") as dst:
                            dst.write(src.read())
            else:
                zf.extractall(source_dir)
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
    Returns True if MAA is usable after the attempt."""
    _log = ctx.log

    # Step 0: Already ready?
    if _is_source_ready(source_dir):
        ver = _detect_version(source_dir)
        if ver != "unknown":
            ctx.config["maa_version"] = ver
        return True

    # If source exists but MAA.exe missing, it's corrupted → clean up
    if source_dir.exists():
        exe = source_dir / "MAA.exe"
        if not exe.exists():
            _log("[MAA] source 目录不完整，清理后重新下载")
            shutil.rmtree(source_dir, ignore_errors=True)
            source_dir.mkdir(parents=True)

    # Step 1: Get download URL
    _log("[MAA] 检查 GitHub 最新版本...")
    result = _get_download_url()
    if not result:
        _log("[MAA] 无法获取下载链接（网络问题或 GitHub 限频），请手动下载")
        source_dir.mkdir(parents=True, exist_ok=True)
        return False
    dl_url, tag = result
    _log(f"[MAA] 发现新版: {tag}")

    # Step 2: Download to temp file
    source_dir.mkdir(parents=True, exist_ok=True)
    temp_zip = source_dir.parent / f"_download_{int(time.time())}.zip"
    if not _download_zip(dl_url, temp_zip, _log):
        temp_zip.unlink(missing_ok=True)
        return False

    # Step 3: Extract
    if not _extract_zip(temp_zip, source_dir, _log):
        temp_zip.unlink(missing_ok=True)
        return False
    temp_zip.unlink(missing_ok=True)

    # Step 4: Init MAA (generate $type)
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
        return True
    else:
        _log("[MAA] MAA 已下载但初始化不完全，请手动运行一次 MAA.exe")
        return False
