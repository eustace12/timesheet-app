"""Language configuration tests."""

import re
from pathlib import Path

from i18n import SUPPORTED_LANGUAGES, translate


def test_interface_is_english_only():
    assert SUPPORTED_LANGUAGES == {"en": "English"}
    assert translate("nav.timesheet", "en") == "Timesheet"
    assert translate("timesheet.generating_pdf", "ru") == "Generating PDF…"


def test_every_template_translation_has_english_text():
    templates = Path(__file__).parents[1] / "templates"
    keys = set()
    for template in templates.glob("*.html"):
        keys.update(re.findall(r"\{\{\s*t\('([^']+)'", template.read_text()))

    missing = {key for key in keys if translate(key, "en") == key}
    assert missing == set()
