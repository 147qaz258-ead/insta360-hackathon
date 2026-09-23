import os
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

from app.config import load_local_environment


class LocalConfigTests(unittest.TestCase):
    def test_loads_values_without_overriding_process_environment(self):
        with TemporaryDirectory() as directory:
            path = Path(directory) / "config.env"
            path.write_text(
                "# local only\nTEST_CONFIG_FIRST=from-file\nTEST_CONFIG_KEEP=file-value\n",
                encoding="utf-8",
            )
            os.environ["TEST_CONFIG_KEEP"] = "process-value"
            try:
                self.assertTrue(load_local_environment(path))
                self.assertEqual(os.environ["TEST_CONFIG_FIRST"], "from-file")
                self.assertEqual(os.environ["TEST_CONFIG_KEEP"], "process-value")
            finally:
                os.environ.pop("TEST_CONFIG_FIRST", None)
                os.environ.pop("TEST_CONFIG_KEEP", None)

    def test_missing_file_is_not_an_error(self):
        self.assertFalse(load_local_environment(Path("missing-config.env")))


if __name__ == "__main__":
    unittest.main()
