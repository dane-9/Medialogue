import pytest


@pytest.fixture
def scratch_cwd(tmp_path, monkeypatch):
    """Run in a per-test temp directory.

    Tests that build fake media roots under ``Path.cwd()`` would otherwise
    leave them in ``backend/`` whenever cleanup fails (a locked file on
    Windows, an interrupted run), and those leftovers end up committed.
    """

    monkeypatch.chdir(tmp_path)
    return tmp_path
