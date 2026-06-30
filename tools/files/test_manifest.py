from pathlib import Path
from unittest import TestCase

from tools.main import validate_tool_package


class FilesManifestTest(TestCase):
    def test_tool_package_is_valid(self) -> None:
        validate_tool_package(Path(__file__).resolve().parent)
