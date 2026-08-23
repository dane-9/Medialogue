import pytest

from app.core.errors import AppError
from app.services.acquisition_paths import join_qbit_path, sanitize_show_folder_name, show_folder_with_year


def test_show_folder_path_uses_category_as_root_and_appends_year_without_parentheses() -> None:
    folder = show_folder_with_year("How I Met Your Mother", 2005, True)
    assert folder == "How I Met Your Mother 2005"
    assert join_qbit_path("/Drive/Shows", folder) == "/Drive/Shows/How I Met Your Mother 2005"


def test_show_folder_path_preserves_custom_name_and_does_not_double_append_year() -> None:
    assert show_folder_with_year("HIMYM 2005", 2005, True) == "HIMYM 2005"
    assert show_folder_with_year("HIMYM", 2005, False) == "HIMYM"


def test_show_folder_sanitizes_one_component_and_preserves_windows_separator() -> None:
    folder = sanitize_show_folder_name('Star Trek: Strange/New Worlds?')
    assert folder == "Star Trek - Strange - New Worlds -"
    assert join_qbit_path(r"D:\Shows", folder) == r"D:\Shows\Star Trek - Strange - New Worlds -"


def test_show_folder_rejects_empty_component_and_missing_year() -> None:
    with pytest.raises(AppError):
        sanitize_show_folder_name("   ")
    with pytest.raises(AppError):
        show_folder_with_year("Unknown Show", None, True)
