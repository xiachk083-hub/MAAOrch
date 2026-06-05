"""Tests for MAAOrch core functions"""
import sys, json
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

import config as config_mod
import utils as utils_mod


class TestConfig:
    def test_make_id(self):
        assert len(utils_mod.make_id()) == 8
        assert utils_mod.make_id() != utils_mod.make_id()

    def test_parse_maa_version(self, tmp_path):
        d = tmp_path / "MAA-v5.12.0" / "sub"; d.mkdir(parents=True)
        assert utils_mod.parse_maa_version(d) == "v5.12.0"
        d2 = tmp_path / "v6.11.1" / "sub"; d2.mkdir(parents=True)
        assert utils_mod.parse_maa_version(d2) == "v6.11.1"
        assert utils_mod.parse_maa_version(tmp_path) is None

    def test_version_tuple(self):
        assert utils_mod._version_tuple("v5.12.0") == (5, 12, 0)
        assert utils_mod._version_tuple("6.11.1") == (6, 11, 1)
        assert utils_mod._version_tuple("") == (0,)
        assert utils_mod._version_tuple("invalid") == (0,)

    def test_get_platform_key(self):
        k = utils_mod.get_platform_key()
        assert isinstance(k, str)
        assert k in ("win-x64", "win-arm64")

    def test_load_save_config(self, tmp_path):
        cf = tmp_path / "config.json"
        cf.write_text(json.dumps({"version": 5, "accounts": [], "groups": [], "warehouse": []}))
        orig = config_mod.CONFIG_FILE; config_mod.CONFIG_FILE = cf
        try:
            data = config_mod.load_config()
            assert data is not None
            assert data["version"] == 5
            data["test"] = 1
            config_mod.save_config(data)
            loaded = config_mod.load_config()
            assert loaded["test"] == 1
        finally:
            config_mod.CONFIG_FILE = orig


class TestMigration:
    def test_v4_to_v5_warehouse(self):
        data = {"version": 4, "accounts": [], "groups": [],
                "warehouse": [{"path": "C:/MAA/MAA.exe", "args": [], "cwd": "", "env": {}}]}
        result = config_mod.migrate_v4_to_v5(data)
        assert result["version"] == 5
        assert "webhook_url" in result
        assert result["warehouse"][0]["account_ref"] == ""
        assert result["warehouse"][0]["launch_mode"] == "gui"

    def test_v4_to_v5_auto_detect_maa_type(self):
        data = {"version": 4, "accounts": [], "groups": [],
                "warehouse": [{"path": "C:/MAA/MAA.exe", "args": [], "cwd": "", "env": {}}]}
        result = config_mod.migrate_v4_to_v5(data)
        assert result["warehouse"][0]["maa_type"] == "maa"

    def test_v4_to_v5_general_type(self):
        data = {"version": 4, "accounts": [], "groups": [],
                "warehouse": [{"path": "notepad.exe", "args": [], "cwd": "", "env": {}}]}
        result = config_mod.migrate_v4_to_v5(data)
        assert result["warehouse"][0]["maa_type"] == "general"

    def test_v4_to_v5_full(self, tmp_path):
        cf = tmp_path / "config.json"
        orig_cfg = {"version": 4, "accounts": [], "groups": [],
                    "warehouse": [{"path": "notepad.exe", "args": [], "cwd": "", "env": {}}]}
        cf.write_text(json.dumps(orig_cfg))
        orig = config_mod.CONFIG_FILE; config_mod.CONFIG_FILE = cf
        try:
            data = config_mod.load_config()
            assert data["version"] == 5
            assert len(data["warehouse"]) == 1
        finally:
            config_mod.CONFIG_FILE = orig


class TestAdbSanitize:
    def test_broken_addr_fixed(self, tmp_path):
        """load_config should fix 27.0.0.1 → 127.0.0.1"""
        cf = tmp_path / "config.json"
        cf.write_text(json.dumps({"version": 5, "accounts": [{"adb_address": "27.0.0.1:16416"}],
                                  "groups": [], "warehouse": []}))
        orig = config_mod.CONFIG_FILE; config_mod.CONFIG_FILE = cf
        try:
            data = config_mod.load_config()
            assert data["accounts"][0]["adb_address"] == "127.0.0.1:16416"
        finally:
            config_mod.CONFIG_FILE = orig

    def test_good_addr_unchanged(self, tmp_path):
        cf = tmp_path / "config.json"
        cf.write_text(json.dumps({"version": 5, "accounts": [{"adb_address": "127.0.0.1:7555"}],
                                  "groups": [], "warehouse": []}))
        orig = config_mod.CONFIG_FILE; config_mod.CONFIG_FILE = cf
        try:
            data = config_mod.load_config()
            assert data["accounts"][0]["adb_address"] == "127.0.0.1:7555"
        finally:
            config_mod.CONFIG_FILE = orig

    def test_empty_addr(self, tmp_path):
        cf = tmp_path / "config.json"
        cf.write_text(json.dumps({"version": 5, "accounts": [{"adb_address": ""}],
                                  "groups": [], "warehouse": []}))
        orig = config_mod.CONFIG_FILE; config_mod.CONFIG_FILE = cf
        try:
            data = config_mod.load_config()
            assert data["accounts"][0]["adb_address"] == ""
        finally:
            config_mod.CONFIG_FILE = orig



