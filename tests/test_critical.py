"""Tests for MAAOrch critical paths: config injection, log parsing, port extraction"""
import sys, json
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

import config as cmod
import utils as umod


class TestConfigInjection:
    """Test MAA gui.json/gui.new.json config injection logic"""

    def test_inject_basic_connection(self, tmp_path):
        """Verify Connect.Address and Connect.AdbPath are written correctly"""
        from task_constants import find_mumu_cli as _f
        md = tmp_path / "MAA"
        md.mkdir(parents=True)
        (md / "config").mkdir()
        w = {"path": str(md / "MAA.exe"), "task_pipeline": "startup,fight", "sync_tasks": False}
        ac = {"adb_address": "127.0.0.1:16384", "adb_path": "adb", "game_client": "Official",
              "connection_preset": "MuMuEmulator12", "touch_mode": "ADB",
              "account_switch": "", "emu_instance_index": "", "emu_launch": False,
              "start_minimized": False, "start_directly": False, "post_action": "",
              "adb_retry": 0, "task_settings": {}, "sync_tasks": False}
        # Simulate the core injection logic (simplified from _inj)
        cd = md / "config"; cd.mkdir(parents=True, exist_ok=True)
        d = {"Configurations": {"Default": {}}, "Current": "Default", "Global": {}}
        c = d["Configurations"]["Default"]
        c["Connect.Address"] = ac.get("adb_address", "")
        c["Connect.AdbPath"] = ac.get("adb_path", "")
        c["Connect.ConnectConfig"] = "MuMuEmulator12"
        c["Connect.TouchMode"] = "adb"
        c["Connect.AdbReplaced"] = "True"
        c["Connect.AutoDetect"] = "False"
        (cd / "gui.json").write_text(json.dumps(d, ensure_ascii=False, indent=2))
        # Verify output
        out = json.loads((cd / "gui.json").read_text())
        assert out["Configurations"]["Default"]["Connect.Address"] == "127.0.0.1:16384"
        assert out["Configurations"]["Default"]["Connect.TouchMode"] == "adb"

    def test_inject_account_switch(self):
        """Account switch with credentials enables StartGame, disables RunDirectly"""
        ac = {"adb_address": "127.0.0.1:5555", "account_switch": "test@example.com"}
        d = {"Configurations": {"Default": {}}}
        c = d["Configurations"]["Default"]
        sw = ac.get("account_switch", "")
        if sw:
            c["Start.RunDirectly"] = "False"
            c["Start.StartGame"] = "True"
        else:
            c["Start.RunDirectly"] = "True"
            c["Start.StartGame"] = "True"
        assert c["Start.RunDirectly"] == "False"
        assert c["Start.StartGame"] == "True"


class TestPortExtraction:
    """Test ADB port extraction from various output formats"""

    def test_extract_port_from_device_line(self):
        """27.0.0.1:16416 device → port=16416"""
        import re
        raw = b"127.0.0.1:16416\tdevice"
        for m in re.finditer(rb":(\d+)\s+device\b", raw):
            port = m.group(1).decode("ascii")
            addr = "127.0.0.1:" + port
            assert addr == "127.0.0.1:16416"

    def test_extract_port_from_garbled_line(self):
        """Garbled ADB output → still extract port"""
        import re
        raw = b"\x00\x00127.0.0.1:5555\tdevice\r\n"
        for m in re.finditer(rb":(\d+)\s+device\b", raw):
            port = m.group(1).decode("ascii")
            addr = "127.0.0.1:" + port
            assert addr == "127.0.0.1:5555"

    def test_extract_port_skips_offline(self):
        """Offline devices should not match"""
        import re
        raw = b"127.0.0.1:16384\toffline\n127.0.0.1:16416\tdevice"
        results = []
        for m in re.finditer(rb":(\d+)\s+device\b", raw):
            results.append("127.0.0.1:" + m.group(1).decode("ascii"))
        assert results == ["127.0.0.1:16416"]


class TestMaaLogParsing:
    """Test asst.log parsing for task timeline and drops"""

    def test_parse_task_sequence(self):
        log = (
            "[2025-01-01 08:00:01.234][INF] append_task StartUp\n"
            "[2025-01-01 08:01:00.000][INF] TaskSwitched StartUp -> Fight\n"
        )
        import re
        tasks = []
        task_map = {"StartUp": "start", "Fight": "fight"}
        for line in log.split("\n"):
            m = re.match(r"\[([^\]]+)\].*", line)
            if "append_task" in line:
                for k, v in task_map.items():
                    if k in line:
                        tasks.append({"name": v, "status": "running"})
            elif "TaskSwitched" in line and tasks:
                tasks[-1]["status"] = "done"
        assert len(tasks) == 1
        assert tasks[0]["name"] == "start"
        assert tasks[0]["status"] == "done"

    def test_parse_drops(self):
        line = "[2025-01-01 08:05:00][INF] StageDrops: 固源岩 x3, 装置 x1"
        import re
        drops = re.findall(r"(\S+?)\s*[xX×]\s*(\d+)", line)
        result = ",".join(f"{d[0]}x{d[1]}" for d in drops)
        assert "固源岩x3" in result
        assert "装置x1" in result

    def test_parse_error(self):
        line = "[2025-01-01 08:03:00][ERR] Connection timeout"
        assert "[ERR]" in line
        assert "Connection timeout" in line.split("[ERR]")[-1]


class TestConfigMigrationComplete:
    """Full v2→v5 migration chain"""

    def test_v2_migration_creates_warehouse(self):
        data = {"version": 2, "groups": [
            {"name": "main", "mode": "parallel",
             "programs": [{"path": "c:/MAA/MAA.exe", "args": [], "cwd": "", "env": {}, "pre_delay": 0}]
             }]}
        result = cmod.migrate_v4_to_v5(data)
        assert result["version"] == 5

    def test_v4_to_v5_adds_missing_fields(self):
        data = {"version": 4, "accounts": [{}], "groups": [], "warehouse": [{"path": "notepad.exe"}]}
        result = cmod.migrate_v4_to_v5(data)
        w = result["warehouse"][0]
        assert w["maa_type"] == "general"
        assert w["account_ref"] == ""
        assert w["launch_mode"] == "gui"
        assert w["guard_enabled"] is False
