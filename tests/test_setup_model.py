from jiri import setup


def test_download_model_resolves_known_repository(monkeypatch) -> None:
    downloaded = []
    loaded = []

    class FakeHub:
        @staticmethod
        def snapshot_download(repository):
            downloaded.append(repository)
            return "/tmp/faster-whisper-large-v3"

    class FakeWhisper:
        def __init__(self, path, device, compute_type):
            loaded.append((path, device, compute_type))

    monkeypatch.setitem(__import__("sys").modules, "huggingface_hub", FakeHub)
    monkeypatch.setitem(__import__("sys").modules, "faster_whisper", type("FakeModule", (), {"WhisperModel": FakeWhisper}))

    setup.download_model("large-v3")

    assert downloaded == ["Systran/faster-whisper-large-v3"]
    assert loaded == [("/tmp/faster-whisper-large-v3", "auto", "auto")]
