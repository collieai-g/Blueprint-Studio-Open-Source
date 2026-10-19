"""The setup planner writes private instructions, not pretend deployments."""

import json
import os
from pathlib import Path
import shlex
import tempfile
import unittest
from unittest.mock import patch

from blueprint_library.onboarding import installation_instruction, load_company_file, prepare_setup
from blueprint_library.store import LibraryError


class OnboardingTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="blueprint-setup-")
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.output = self.root / "公司私人安裝"
        self.company = {"name": "  藍圖公司  ", "industry": "AI 工具", "description": "公司簡介", "goals": "做好文件及收藏"}

    def assert_error(self, callback, status=None):
        with self.assertRaises(LibraryError) as caught:
            callback()
        if status is not None:
            self.assertEqual(caught.exception.status, status)

    def test_default_cloud_plan_is_private_and_does_not_initialize_any_service(self):
        result = prepare_setup(self.output, self.company)
        plan = json.loads((self.output / "setup.json").read_text())
        self.assertEqual(plan["schema"], "blueprint-setup")
        self.assertEqual((plan["mode"], plan["provider"], plan["status"]), ("cloud", "render", "awaiting_cloud_deployment"))
        self.assertEqual(plan["version"], 1)
        self.assertEqual(result["setup_file"], str(self.output / "setup.json"))
        self.assertEqual(plan["company_file"], str(self.output / "company-profile.json"))
        self.assertNotIn("data_path", plan)
        self.assertNotIn("url", plan)
        self.assertFalse(Path(plan["workspace_path"]).exists())
        profile = load_company_file(plan["company_file"])
        self.assertEqual(profile, {**self.company, "name": "藍圖公司"})
        self.assertNotIn("expected_version", profile)
        self.assertEqual(sorted(path.name for path in self.output.iterdir()), ["START_HERE.md", "company-profile.json", "deployment.env.example", "setup.json"])
        self.assertFalse(list(self.output.rglob("*.sqlite3")))
        self.assertFalse(list(self.output.rglob("access.json")))
        if os.name != "nt":
            self.assertEqual(self.output.stat().st_mode & 0o777, 0o700)
            self.assertTrue(all(path.stat().st_mode & 0o777 == 0o600 for path in self.output.iterdir()))

    def test_provider_guides_require_actual_url_login_save_and_restart(self):
        for provider in ("render", "railway", "server"):
            with self.subTest(provider=provider):
                output = self.root / provider
                result = prepare_setup(output, self.company, provider=provider, username="company-owner")
                guide = Path(result["start_here"]).read_text()
                self.assertIn("company/Dockerfile", guide)
                self.assertIn("/data", guide)
                self.assertIn("HTTPS", guide)
                self.assertIn("費用", guide)
                self.assertIn("--url https://actual", guide)
                self.assertIn("--username-env BLUEPRINT_BACKEND_USERNAME", guide)
                self.assertIn("--password-env BLUEPRINT_BACKEND_PASSWORD", guide)
                self.assertIn("同一筆紀錄", guide)
                self.assertIn("重新啟動", guide)
                self.assertNotIn("已連接成功", guide)
                if provider == "render":
                    self.assertIn("https://render.com/deploy?repo=https://github.com/collieai-g/blueprint-folder-agent", guide)
                elif provider == "railway":
                    self.assertIn("https://railway.com/new", guide)
                    self.assertIn("Volume", guide)
                else:
                    self.assertIn("docker volume create company-data", guide)
                    self.assertIn("127.0.0.1:8787:8787", guide)

    def test_local_plan_uses_quoted_private_paths_and_no_cloud_provider(self):
        output = self.root / "my 'private' setup"
        result = prepare_setup(output, {"name": "公司"}, mode="local", username="owner name")
        self.assertEqual((result["mode"], result["status"]), ("local", "awaiting_local_start"))
        self.assertNotIn("provider", result)
        self.assertEqual(result["data_path"], str(output / "data"))
        self.assertFalse(Path(result["data_path"]).exists())
        guide = Path(result["start_here"]).read_text()
        self.assertIn("library-init " + shlex.quote(result["data_path"]), guide)
        self.assertIn("--username " + shlex.quote("owner name"), guide)
        self.assertIn("--company-file " + shlex.quote(result["company_file"]), guide)
        self.assertIn("workday-connect " + shlex.quote(result["workspace_path"]), guide)
        self.assertIn("library --data " + shlex.quote(result["data_path"]), guide)
        self.assertNotIn("--url", guide)

    def test_env_values_are_quoted_without_secrets_or_newline_injection(self):
        company = {"name": "公司\n名字", "description": '一行 "說明"\nBLUEPRINT_ADMIN_PASSWORD=not-a-secret'}
        result = prepare_setup(self.output, company)
        env = Path(result["env_file"]).read_text()
        values = dict(line.split("=", 1) for line in env.splitlines() if line and not line.startswith("#"))
        self.assertEqual(values["BLUEPRINT_ADMIN_PASSWORD"], "")
        self.assertEqual(json.loads(values["BLUEPRINT_COMPANY_NAME"]), company["name"])
        self.assertEqual(json.loads(values["BLUEPRINT_COMPANY_DESCRIPTION"]), company["description"])
        self.assertNotIn("BLUEPRINT_BACKEND_PASSWORD", values)
        self.assertEqual(json.loads(values["BLUEPRINT_DATA_DIR"]), "/data")

    def test_identical_rerun_is_idempotent_but_conflicting_plan_is_not_overwritten(self):
        first = prepare_setup(self.output, self.company)
        before = {path.name: (path.read_bytes(), path.stat().st_mtime_ns) for path in self.output.iterdir()}
        self.assertEqual(prepare_setup(self.output, self.company), first)
        self.assertEqual({path.name: (path.read_bytes(), path.stat().st_mtime_ns) for path in self.output.iterdir()}, before)
        for kwargs in ({"company": {"name": "另一間公司"}}, {"mode": "local"}, {"provider": "railway"}, {"username": "other-owner"}):
            arguments = {"company": self.company, **kwargs}
            self.assert_error(lambda: prepare_setup(self.output, **arguments), 409)
        self.assertEqual({path.name: (path.read_bytes(), path.stat().st_mtime_ns) for path in self.output.iterdir()}, before)

    def test_preflight_preserves_custom_files_before_writing_any_company_input(self):
        self.output.mkdir()
        (self.output / "START_HERE.md").write_text("我的自訂指引")
        (self.output / "existing.txt").write_text("保留")
        self.assert_error(lambda: prepare_setup(self.output, self.company), 409)
        self.assertEqual(sorted(path.name for path in self.output.iterdir()), ["START_HERE.md", "existing.txt"])
        self.assertEqual((self.output / "START_HERE.md").read_text(), "我的自訂指引")

    def test_invalid_company_mode_provider_and_username_do_not_create_output(self):
        invalid = [None, [], {}, {"name": ""}, {"name": "x" * 121}, {"name": "公司", "industry": "x" * 121},
                   {"name": "公司", "description": "x" * 4001}, {"name": "公司", "goals": "x" * 4001},
                   {"name": "公司", "description": None}, {"name": "公司\x00"}, {"name": "公司", "expected_version": 0}]
        for company in invalid:
            self.assert_error(lambda: prepare_setup(self.output, company))
            self.assertFalse(self.output.exists())
        for kwargs in ({"mode": "remote"}, {"mode": []}, {"provider": "fake"}, {"provider": []}, {"username": ""}, {"username": "owner:name"}, {"username": "owner\nname"}):
            self.assert_error(lambda: prepare_setup(self.output, self.company, **kwargs))
            self.assertFalse(self.output.exists())

    def test_private_paths_cannot_overlap_the_public_source_in_either_direction(self):
        source = self.root / "source"
        source.mkdir()
        with patch("blueprint_library.onboarding._SOURCE_ROOT", source):
            for output in (source, source / "private", self.root):
                self.assert_error(lambda: prepare_setup(output, self.company))
            inside = source / "company-profile.json"
            inside.write_text(json.dumps({"name": "private"}))
            self.assert_error(lambda: load_company_file(inside))
        self.assertFalse((source / "private").exists())

    def test_symlink_output_ancestors_targets_and_company_files_are_rejected(self):
        actual = self.root / "actual"
        actual.mkdir()
        link = self.root / "alias"
        try:
            link.symlink_to(actual, target_is_directory=True)
        except OSError:
            self.skipTest("Symlinks are unavailable on this filesystem")
        self.assert_error(lambda: prepare_setup(link / "nested", self.company))
        self.assertFalse((actual / "nested").exists())
        target = actual / "profile.json"
        target.write_text(json.dumps({"name": "existing"}))
        self.output.mkdir()
        (self.output / "company-profile.json").symlink_to(target)
        self.assert_error(lambda: prepare_setup(self.output, self.company))
        self.assertEqual(json.loads(target.read_text()), {"name": "existing"})
        self.assert_error(lambda: load_company_file(self.output / "company-profile.json"))
        self.assert_error(lambda: load_company_file(link / "profile.json"))
        dangling = self.root / "dangling"
        dangling.symlink_to(self.root / "missing", target_is_directory=True)
        self.assert_error(lambda: prepare_setup(dangling, self.company))

    def test_company_loader_validates_json_shape_size_and_types(self):
        path = self.root / "company.json"
        path.write_text('\ufeff' + json.dumps({"name": " 公司 ", "goals": "目標"}))
        self.assertEqual(load_company_file(path), {"name": "公司", "goals": "目標"})
        for value in ("not json", "[]", '{"name":"公司","expected_version":0}', '{"name":"公司","industry":null}', " " * (64 * 1024 + 1)):
            path.write_text(value)
            self.assert_error(lambda: load_company_file(path))
        self.assert_error(lambda: load_company_file(self.root))
        self.assert_error(lambda: load_company_file(self.root / "missing.json"))

    def test_instruction_is_universal_and_explicit_about_tool_limits(self):
        instruction = installation_instruction()
        for required in ("docs/INSTALL.md", "AGENTS.md", "公司名稱", "行業", "簡介", "目標", "雲端", "本機",
                         "Render", "Railway", "HTTPS", "持久儲存", "費用", "秘密設定", "今日收工", "去重", "版本", "歷史", "JSON"):
            self.assertIn(required, instruction)
        self.assertIn("目前可見或我提供", instruction)
        self.assertIn("普通聊天沒有工具", instruction)
        self.assertIn("不保存秘密或整段原始聊天", instruction)
        self.assertIn("重啟後查回", instruction)
        self.assertIn("保留已有資料", instruction)


if __name__ == "__main__":
    unittest.main()
