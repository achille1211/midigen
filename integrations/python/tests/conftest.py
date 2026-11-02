import os
import tempfile

import pytest

# Set before any (module-scoped) fixture builds a song: tests must never read or write the user's
# midigen learning store (~/.midigen/experience), or learned weights would change generated output.
os.environ["MIDIGEN_DATA_DIR"] = tempfile.mkdtemp(prefix="midigen-int-test-")
os.environ.pop("MIDIGEN_LEARN", None)


@pytest.fixture(autouse=True)
def _no_learn_env(monkeypatch):
    monkeypatch.delenv("MIDIGEN_LEARN", raising=False)
