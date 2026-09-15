import os
import time
from datetime import datetime
from pathlib import Path

from PIL import Image

import copy_photos as cp


def _make_plain_jpeg(path: Path, mtime: datetime) -> None:
    Image.new("RGB", (4, 4), "red").save(path)
    ts = time.mktime(mtime.timetuple())
    os.utime(path, (ts, ts))


def _make_exif_jpeg(path: Path, exif_date: datetime) -> None:
    exif = Image.Exif()
    exif[36867] = exif_date.strftime(cp.EXIF_DATE_FORMAT)  # DateTimeOriginal
    Image.new("RGB", (4, 4), "blue").save(path, exif=exif.tobytes())


def test_classify():
    assert cp.classify(Path("IMG_0001.JPG")) == "pic"
    assert cp.classify(Path("clip.MOV")) == "video"
    assert cp.classify(Path("notes.txt")) is None


def test_get_photo_date_uses_exif_when_present(tmp_path):
    path = tmp_path / "with_exif.jpg"
    exif_date = datetime(2022, 3, 14, 9, 30, 0)
    _make_exif_jpeg(path, exif_date)
    assert cp.get_photo_date(path) == exif_date


def test_get_photo_date_falls_back_to_mtime(tmp_path):
    path = tmp_path / "no_exif.jpg"
    mtime = datetime(2021, 6, 1, 12, 0, 0)
    _make_plain_jpeg(path, mtime)
    result = cp.get_photo_date(path)
    assert (result.year, result.month, result.day) == (2021, 6, 1)


def test_destination_dir_layout(tmp_path):
    date = datetime(2023, 7, 4, 10, 20, 30)
    result = cp.destination_dir(tmp_path, date, "pic")
    assert result == tmp_path / "2023" / "2023-07" / "2023-07-04" / "pic"


def test_resolve_destination_new_file(tmp_path):
    dest_dir = tmp_path / "dest"
    dest_dir.mkdir()
    src = tmp_path / "IMG_0001.jpg"
    src.write_bytes(b"hello")
    resolved = cp.resolve_destination(src, dest_dir)
    assert resolved.path == dest_dir / "IMG_0001.jpg"
    assert resolved.already_present is False


def test_resolve_destination_skips_identical(tmp_path):
    dest_dir = tmp_path / "dest"
    dest_dir.mkdir()
    src = tmp_path / "IMG_0001.jpg"
    src.write_bytes(b"hello")
    (dest_dir / "IMG_0001.jpg").write_bytes(b"hello")
    resolved = cp.resolve_destination(src, dest_dir)
    assert resolved.path == dest_dir / "IMG_0001.jpg"
    assert resolved.already_present is True


def test_resolve_destination_renames_on_conflict(tmp_path):
    dest_dir = tmp_path / "dest"
    dest_dir.mkdir()
    src = tmp_path / "IMG_0001.jpg"
    src.write_bytes(b"new content")
    (dest_dir / "IMG_0001.jpg").write_bytes(b"old content")
    resolved = cp.resolve_destination(src, dest_dir)
    assert resolved.path == dest_dir / "IMG_0001_2.jpg"
    assert resolved.already_present is False


def test_verify_identical(tmp_path):
    a = tmp_path / "a.bin"
    b = tmp_path / "b.bin"
    c = tmp_path / "c.bin"
    a.write_bytes(b"same content")
    b.write_bytes(b"same content")
    c.write_bytes(b"different content")
    assert cp.verify_identical(a, b) is True
    assert cp.verify_identical(a, c) is False


def test_copy_media_end_to_end(tmp_path):
    source = tmp_path / "sd_card"
    source.mkdir()
    dest = tmp_path / "hdd"
    dest.mkdir()

    photo = source / "IMG_0001.jpg"
    exif_date = datetime(2024, 1, 15, 8, 0, 0)
    _make_exif_jpeg(photo, exif_date)

    video = source / "CLIP_0001.mp4"
    video.write_bytes(b"fake video data")
    ts = time.mktime(datetime(2024, 1, 15, 9, 0, 0).timetuple())
    os.utime(video, (ts, ts))

    other = source / "readme.txt"
    other.write_text("not media")

    stats = cp.copy_media(source, dest)

    assert stats.total == 2  # readme.txt is ignored
    assert stats.copied == 2
    assert stats.errors == 0

    assert (dest / "2024" / "2024-01" / "2024-01-15" / "pic" / "IMG_0001.jpg").exists()
    assert (dest / "2024" / "2024-01" / "2024-01-15" / "video" / "CLIP_0001.mp4").exists()

    # Re-running should skip the already-copied identical files.
    stats2 = cp.copy_media(source, dest)
    assert stats2.copied == 0
    assert stats2.skipped_identical == 2


def test_copy_media_delete_source_after_verified_copy(tmp_path):
    source = tmp_path / "sd_card"
    source.mkdir()
    dest = tmp_path / "hdd"
    dest.mkdir()

    photo = source / "IMG_0001.jpg"
    _make_exif_jpeg(photo, datetime(2024, 1, 15, 8, 0, 0))

    stats = cp.copy_media(source, dest, delete_source=True)

    assert stats.copied == 1
    assert stats.deleted == 1
    assert stats.errors == 0
    assert not photo.exists()
    assert (dest / "2024" / "2024-01" / "2024-01-15" / "pic" / "IMG_0001.jpg").exists()


def test_copy_media_delete_source_also_removes_already_present_files(tmp_path):
    source = tmp_path / "sd_card"
    source.mkdir()
    dest = tmp_path / "hdd"
    dest.mkdir()

    photo = source / "IMG_0001.jpg"
    date = datetime(2024, 1, 15, 8, 0, 0)
    _make_exif_jpeg(photo, date)

    # First pass copies the file to the destination (no deletion requested).
    cp.copy_media(source, dest)
    assert photo.exists()

    # Second pass: file already present and identical -> still eligible for
    # deletion from the source once re-verified by hash.
    stats = cp.copy_media(source, dest, delete_source=True)
    assert stats.skipped_identical == 1
    assert stats.deleted == 1
    assert not photo.exists()
