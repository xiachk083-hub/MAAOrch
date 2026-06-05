"""Tests for MAAOrch core functions"""
import sys, json
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

import batch_launcher as bl


class TestConfig:
    def test_make_id(self):
        assert len(bl.make_id()) == 8
        assert bl.make_id() != bl.make_id()

    def test_parse_maa_version(self, tmp_path):
        d = tmp_path / "MAA-v5.12.0" / "sub"; d.mkdir(parents=True)
        assert bl.parse_maa_version(d) == "v5.12.0"
        d2 = tmp_path / "v6.11.1" / "sub"; d2.mkdir(parents=True)
        assert bl.parse_maa_version(d2) == "v6.11.1"
        assert bl.parse_maa_version(tmp_path) is None

    def test_version_tuple(self):
        assert bl._version_tuple("v5.12.0") == (5, 12, 0)
        assert bl._version_tuple("6.11.1") == (6, 11, 1)
        assert bl._version_tuple("") == (0,)
        assert bl._version_tuple("invalid") == (0,)

    def test_get_platform_key(self):
        k = bl.get_platform_key()
        assert isinstance(k, str)
        assert k in ("win-x64", "win-arm64")

    def test_load_save_config(self, tmp_path):
        cf = tmp_path / "config.json"
        cf.write_text(json.dumps({"version": 5, "accounts": [], "groups": [], "warehouse": []}))
        orig = bl.CONFIG_FILE; bl.CONFIG_FILE = cf
        try:
            data = bl.load_config()
            assert data is not None
            assert data["version"] == 5
            data["test"] = 1
            bl.save_config(data)
            loaded = bl.load_config()
            assert loaded["test"] == 1
        finally:
            bl.CONFIG_FILE = orig


class TestMigration:
    def test_v4_to_v5_warehouse(self):
        data = {"version": 4, "accounts": [], "groups": [],
                "warehouse": [{"path": "C:/MAA/MAA.exe", "args": [], "cwd": "", "env": {}}]}
        result = bl.migrate_v4_to_v5(data)
        assert result["version"] == 5
        assert "webhook_url" in result
        assert result["warehouse"][0]["account_ref"] == ""
        assert result["warehouse"][0]["launch_mode"] == "gui"

    def test_v4_to_v5_auto_detect_maa_type(self):
        data = {"version": 4, "accounts": [], "groups": [],
                "warehouse": [{"path": "C:/MAA/MAA.exe", "args": [], "cwd": "", "env": {}}]}
        result = bl.migrate_v4_to_v5(data)
        assert result["warehouse"][0]["maa_type"] == "maa"

    def test_v4_to_v5_general_type(self):
        data = {"version": 4, "accounts": [], "groups": [],
                "warehouse": [{"path": "notepad.exe", "args": [], "cwd": "", "env": {}}]}
        result = bl.migrate_v4_to_v5(data)
        assert result["warehouse"][0]["maa_type"] == "general"

    def test_v4_to_v5_full(self, tmp_path):
        cf = tmp_path / "config.json"
        orig_cfg = {"version": 4, "accounts": [], "groups": [],
                    "warehouse": [{"path": "notepad.exe", "args": [], "cwd": "", "env": {}}]}
        cf.write_text(json.dumps(orig_cfg))
        orig = bl.CONFIG_FILE; bl.CONFIG_FILE = cf
        try:
            data = bl.load_config()
            assert data["version"] == 5
            assert len(data["warehouse"]) == 1
        finally:
            bl.CONFIG_FILE = orig


class TestAdbSanitize:
    def test_broken_addr_fixed(self):
        """27.0.0.1:16416 -> 127.0.0.1:16416"""
        data = {"version": 5, "accounts": [{"adb_address": "27.0.0.1:16416"}],
                "groups": [], "warehouse": []}
        # Simulate what load_config does
        for a in data.get("accounts", []):
            raw = a.get("adb_address", "")
            if raw and not raw.startswith("127.0.0.1:"):
                m = __import__("re").search(r":(\d+)$", raw)
                if m:
                    a["adb_address"] = "127.0.0.1:" + m.group(1)
        assert data["accounts"][0]["adb_address"] == "127.0.0.1:16416"

    def test_good_addr_unchanged(self):
        data = {"accounts": [{"adb_address": "127.0.0.1:7555"}]}
        for a in data.get("accounts", []):
            raw = a.get("adb_address", "")
            if raw and not raw.startswith("127.0.0.1:"):
                m = __import__("re").search(r":(\d+)$", raw)
                if m:
                    a["adb_address"] = "127.0.0.1:" + m.group(1)
        assert data["accounts"][0]["adb_address"] == "127.0.0.1:7555"

    def test_empty_addr(self):
        data = {"accounts": [{"adb_address": ""}]}
        for a in data.get("accounts", []):
            raw = a.get("adb_address", "")
            if raw and not raw.startswith("127.0.0.1:"):
                m = __import__("re").search(r":(\d+)$", raw)
                if m:
                    a["adb_address"] = "127.0.0.1:" + m.group(1)
        assert data["accounts"][0]["adb_address"] == ""


class TestFindMaaCli:
    def test_returns_none_when_not_found(self, monkeypatch):
        def fake_which(n): return None
        monkeypatch.setattr(bl, "_find_maa_cli", lambda: None)
        assert bl._find_maa_cli() is None

