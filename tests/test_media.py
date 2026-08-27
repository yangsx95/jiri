from datetime import datetime
from pathlib import Path

from jiri.media import capture_time, next_archived_name, sha256_file, write_json_atomic


def test_capture_time_uses_media_metadata(tmp_path: Path) -> None:
    source = tmp_path / "IMG_1234.mp4"
    source.write_bytes(b"video")
    value, origin = capture_time(source, {"creation_time": "2026-08-27T12:00:00+08:00"})
    assert value == datetime.fromisoformat("2026-08-27T12:00:00+08:00")
    assert origin == "media_metadata"


def test_capture_time_converts_utc_before_archiving_date(tmp_path: Path) -> None:
    source = tmp_path / "VID_20260721_073506.mp4"
    source.write_bytes(b"video")
    value, origin = capture_time(source, {"creation_time": "2026-07-20T23:44:02Z"})
    assert value.strftime("%Y-%m-%d") == "2026-07-21"
    assert origin == "media_metadata"


def test_capture_time_falls_back_to_filename(tmp_path: Path) -> None:
    source = tmp_path / "IMG_2026-08-27.mp4"
    source.write_bytes(b"video")
    value, origin = capture_time(source, {})
    assert value.strftime("%Y-%m-%d") == "2026-08-27"
    assert origin == "filename"


def test_next_archived_name_skips_existing_numbers(tmp_path: Path) -> None:
    (tmp_path / "2026-08-27-001.mp4").write_bytes(b"")
    (tmp_path / "2026-08-27-003.mp4").write_bytes(b"")
    assert next_archived_name(tmp_path, datetime(2026, 8, 27)) == "2026-08-27-002.mp4"


def test_next_archived_name_preserves_video_extension(tmp_path: Path) -> None:
    assert next_archived_name(tmp_path, datetime(2026, 8, 27), ".mov") == "2026-08-27-001.mov"


def test_next_archived_name_reserves_names_from_same_batch(tmp_path: Path) -> None:
    reserved = set()
    first = next_archived_name(tmp_path, datetime(2026, 8, 27), reserved_names=reserved)
    reserved.add(first)
    second = next_archived_name(tmp_path, datetime(2026, 8, 27), reserved_names=reserved)
    assert (first, second) == ("2026-08-27-001.mp4", "2026-08-27-002.mp4")


def test_hash_and_atomic_json(tmp_path: Path) -> None:
    source = tmp_path / "video.mp4"
    source.write_bytes(b"video")
    target = tmp_path / "video.json"
    write_json_atomic(target, {"sha256": sha256_file(source)})
    assert target.exists()
    assert ".tmp" not in {path.name for path in tmp_path.iterdir()}
