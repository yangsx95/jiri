from pathlib import Path

from jiri import setup


def test_install_uses_uv_when_available(monkeypatch) -> None:
    commands = []
    monkeypatch.setattr(setup, "which", lambda name: "/opt/homebrew/bin/uv")
    monkeypatch.setattr(setup.subprocess, "run", lambda command, check: commands.append((command, check)))

    setup.install_transcription_backend()

    command, check = commands[0]
    assert command[:4] == ["/opt/homebrew/bin/uv", "pip", "install", "--python"]
    assert command[-1] == setup.TRANSCRIPTION_PACKAGE
    assert check is True
