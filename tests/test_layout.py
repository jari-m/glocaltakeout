from datetime import datetime

from glocaltakeout.layout import (
    album_relative_dir,
    duplicate_filename,
    folder_matches_album,
    next_duplicate_filename,
    photo_relative_dir,
    split_duplicate_name,
)


def test_photo_dir_uses_year_and_month():
    taken = datetime(2026, 9, 24, 15, 1)
    assert str(photo_relative_dir(taken)).replace("\\", "/") == "photos/2026/09"


def test_duplicate_suffix_round_trip():
    assert split_duplicate_name("IMG_0001.jpg") == ("IMG_0001.jpg", 0)
    assert split_duplicate_name("IMG_0001 (2).jpg") == ("IMG_0001.jpg", 2)
    assert duplicate_filename("IMG_0001.jpg", 0) == "IMG_0001.jpg"
    assert duplicate_filename("IMG_0001.jpg", 3) == "IMG_0001 (3).jpg"


def test_next_duplicate_starts_at_one_when_original_exists():
    name, number = next_duplicate_filename(
        "IMG_0001.jpg",
        ["IMG_0001.jpg", "other.jpg"],
    )
    assert (name, number) == ("IMG_0001 (1).jpg", 1)


def test_next_duplicate_uses_highest_existing_number():
    name, number = next_duplicate_filename(
        "IMG_0001.jpg",
        ["IMG_0001.jpg", "IMG_0001 (1).jpg", "IMG_0001 (4).jpg"],
    )
    assert (name, number) == ("IMG_0001 (5).jpg", 5)


def test_next_duplicate_can_ignore_case():
    name, number = next_duplicate_filename(
        "IMG_0001.JPG",
        ["img_0001.jpg"],
        case_insensitive=True,
    )
    assert number == 1
    assert name == "IMG_0001 (1).JPG"


def test_album_dir_uses_newest_month_and_title():
    newest = datetime(2026, 8, 1, 12, 0)
    assert str(album_relative_dir("Summer trip", newest)).replace("\\", "/") == (
        "albums/2026/08 Summer trip"
    )


def test_existing_album_folder_matches_by_title_suffix():
    assert folder_matches_album("08 Summer trip", "Summer trip")
    assert folder_matches_album("Summer trip", "Summer trip")
    assert not folder_matches_album("08 Summer trip", "Winter trip")
