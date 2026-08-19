from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from app.discovery import iter_discover


class DiscoveryProgressMetricsCP39(unittest.TestCase):
    def test_entries_are_counted_incrementally_including_non_candidates(self):
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory);(root/"audio.mp3").write_bytes(b"x");(root/"note.txt").write_text("x",encoding="utf-8");(root/"nested").mkdir();(root/"nested"/"other.bin").write_bytes(b"x")
            metrics={};observed=[];found=list(iter_discover([str(root)],root,metrics=metrics,on_entry=lambda row:observed.append(row["entries_scanned"])))
            self.assertEqual([path.name for path in found],["audio.mp3","note.txt","other.bin"])
            self.assertEqual((metrics["entries_scanned"],observed), (4,[1,2,3,4]))


if __name__=="__main__":unittest.main()
