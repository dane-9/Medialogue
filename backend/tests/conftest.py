import os

import pytest

# Live folder watching holds directory handles open (on Windows that blocks
# deleting a test's temporary media folders). Tests drive it directly instead.
os.environ.setdefault("MEDIALOGUE_WATCH_MEDIA_FOLDERS", "false")
# Tests change the database directly between requests; sharing computed
# results would hide those changes. One test turns it on to check it.
os.environ.setdefault("MEDIALOGUE_REPLACEMENT_RESULTS_SECONDS", "0")


@pytest.fixture
def scratch_cwd(tmp_path, monkeypatch):
    """Run in a per-test temp directory.

    Tests that build fake media roots under ``Path.cwd()`` would otherwise
    leave them in ``backend/`` whenever cleanup fails (a locked file on
    Windows, an interrupted run), and those leftovers end up committed.
    """

    monkeypatch.chdir(tmp_path)
    return tmp_path
