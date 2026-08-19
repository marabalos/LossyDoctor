from __future__ import annotations

import unittest
from pathlib import Path


ROOT=Path(__file__).resolve().parents[1]


class LicensingPolicyCP48(unittest.TestCase):
    def test_gplv3_license_and_project_policy_are_present(self):
        license_text=(ROOT/'LICENSE').read_text(encoding='utf-8')
        notice=(ROOT/'NOTICE').read_text(encoding='utf-8')
        self.assertIn('GNU GENERAL PUBLIC LICENSE',license_text)
        self.assertIn('Version 3, 29 June 2007',license_text)
        self.assertIn('GPL-3.0-or-later',notice)
        self.assertIn('Diego AMEO',notice)
        self.assertIn('SPDX-License-Identifier: GPL-3.0-or-later',notice)

    def test_trademark_and_dependency_notices_preserve_the_v1_policy(self):
        marks=(ROOT/'TRADEMARKS.md').read_text(encoding='utf-8')
        notices=(ROOT/'THIRD_PARTY_NOTICES.md').read_text(encoding='utf-8')
        self.assertIn('no se conceden bajo la',marks)
        self.assertIn('GNU General Public License',marks)
        self.assertIn('Diego AMEO',marks)
        self.assertIn('denominación claramente diferenciada',marks)
        for component in ('uv','CPython','FFmpeg','mpg123'):
            self.assertIn(component,notices)
        self.assertIn('Política de publicación V1',notices)
        self.assertIn('no redistribuye',notices)
        self.assertIn('Condiciones para una eventual redistribución futura',notices)

    def test_v1_downloads_but_does_not_redistribute_third_party_binaries(self):
        notices=(ROOT/'THIRD_PARTY_NOTICES.md').read_text(encoding='utf-8')
        self.assertIn('publicación oficial V1 no redistribuye',notices)
        self.assertIn('ejecutables: la primera preparación los descarga',notices)
        self.assertIn('reutilizan la copia local',notices)
        self.assertIn('Si una publicación futura decidiera incluir alguno',notices)


if __name__=='__main__':unittest.main()
