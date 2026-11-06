"""How a finished upgrade or replacement is described in History."""

from app.services.media_history import _outcome


def test_each_way_a_replacement_ends_reads_differently() -> None:
    assert _outcome("replacement.finished", {"replacement": True}) == "Kept everything"
    assert _outcome("replacement.finished", {"replacement": True, "old_torrents_removed": True}) == "Old torrents retired, files kept"
    assert _outcome("upgrade.cleanup_completed", {"replacement": True}) == "Old files deleted"


def test_a_finished_upgrade_removed_its_old_release() -> None:
    assert _outcome("upgrade.cleanup_completed", {"replacement": False}) == "Old release removed"
