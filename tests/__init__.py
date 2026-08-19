from __future__ import annotations

import os
import tempfile


# Direct `python -m unittest tests.test_...` runs must not write test journals
# into the portable application's operational state directory.
_JOURNAL_TEMP = None
if "LOSSYDOCTOR_JOURNAL_ROOT" not in os.environ:
    _JOURNAL_TEMP = tempfile.TemporaryDirectory(prefix="lossydoctor-test-journal-")
    os.environ["LOSSYDOCTOR_JOURNAL_ROOT"] = _JOURNAL_TEMP.name
