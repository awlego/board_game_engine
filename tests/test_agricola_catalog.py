"""The client card catalog must stay in sync with the card registry."""

import json
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "tools"))

from export_agricola_cards import build_catalog  # noqa: E402

CATALOG_PATH = os.path.join(os.path.dirname(__file__), "..",
                            "client", "games", "agricola_cards.json")


def test_catalog_in_sync():
    with open(CATALOG_PATH) as f:
        on_disk = json.load(f)
    expected = json.loads(json.dumps(build_catalog()))  # normalize tuples
    assert on_disk == expected, (
        "client/games/agricola_cards.json is stale — run "
        "`python tools/export_agricola_cards.py`")
