from __future__ import annotations

import io
import unittest
from pathlib import Path
from unittest.mock import patch

from app.models import Analysis


class ReportPublicationFailureCP35(unittest.TestCase):
    def test_unwritable_report_destination_fails_cleanly_after_analysis(self):
        from app.main import main

        output=io.StringIO()
        with patch("app.main.recover_interrupted_publications",return_value=[]), \
             patch("app.main.discover",return_value=([Path("C:/input/example.aac")],[])), \
             patch("app.main.analyze_file",return_value=Analysis("C:/input/example.aac","example.aac",{})), \
             patch.object(Path,"mkdir",side_effect=PermissionError("access denied")), \
             patch("sys.stdout",new=output):
            self.assertEqual(main(["C:/input/example.aac"]),1)

        text=output.getvalue()
        self.assertIn("no se pudieron publicar los reportes",text)
        self.assertIn("PermissionError",text)


if __name__=="__main__":unittest.main()
