"""Cloud bootstrap respects the platform port and existing private settings."""

import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from company.entrypoint import cloud_settings, main
from blueprint_library.store import Store


class CompanyRuntimeTests(unittest.TestCase):
    def test_platform_port_and_profile_validation_before_initialization(self):
        port, profile = cloud_settings({"PORT": "12345", "BLUEPRINT_COMPANY_NAME": "測試公司",
                                        "BLUEPRINT_COMPANY_INDUSTRY": "設計"})
        self.assertEqual(port, 12345)
        self.assertEqual(profile["name"], "測試公司")
        self.assertEqual(profile["industry"], "設計")
        self.assertEqual(cloud_settings({}), (8787, None))
        for value in ("", "abc", "0", "65536"):
            with self.subTest(value=value), self.assertRaises(ValueError):
                cloud_settings({"PORT": value})
        with self.assertRaises(ValueError):
            cloud_settings({"BLUEPRINT_COMPANY_NAME": "名" * 121})

    def test_restart_keeps_manual_profile_and_original_authentication(self):
        with tempfile.TemporaryDirectory(prefix="blueprint-cloud-bootstrap-") as temporary:
            data = Path(temporary) / "company"
            environment = {"BLUEPRINT_DATA_DIR": str(data), "PORT": "12345",
                           "BLUEPRINT_ADMIN_USERNAME": "owner",
                           "BLUEPRINT_ADMIN_PASSWORD": "temporary-runtime-test-password",
                           "BLUEPRINT_COMPANY_NAME": "首次公司", "BLUEPRINT_COMPANY_INDUSTRY": "設計",
                           "BLUEPRINT_COMPANY_DESCRIPTION": "初始資料", "BLUEPRINT_COMPANY_GOALS": "整理工作"}
            with patch.dict(os.environ, environment), patch("company.entrypoint.serve_library") as serve:
                main()
                serve.assert_called_once_with(data, host="0.0.0.0", port=12345, open_browser=False)
            store = Store(data)
            profile = store.get_company_profile()
            self.assertEqual(profile["name"], "首次公司")
            changed = store.update_company_profile({"expected_version": profile["version"], "name": "人手更新公司"})
            auth = (data / "access.json").read_bytes()
            environment.update(BLUEPRINT_COMPANY_NAME="環境變數的舊名稱", BLUEPRINT_ADMIN_PASSWORD="")
            with patch.dict(os.environ, environment), patch("company.entrypoint.serve_library"):
                main()
            self.assertEqual(Store(data).get_company_profile(), changed)
            self.assertEqual((data / "access.json").read_bytes(), auth)


if __name__ == "__main__":
    unittest.main()
