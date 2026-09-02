"""Smoke tests for recoll-audio-worker - quick sanity checks."""


class TestSmoke:
    """Smoke tests - fast tests that verify basic functionality."""

    def test_import_transcribe(self) -> None:
        """Verify transcribe module imports without error."""
        import transcribe

        assert transcribe is not None

    def test_compute_file_hash_basic(self, tmp_path) -> None:
        """Basic smoke test for compute_file_hash."""
        from transcribe import compute_file_hash

        test_file = tmp_path / "test.bin"
        test_file.write_bytes(b"test")

        h = compute_file_hash(test_file)
        assert len(h) == 32

    def test_load_state_basic(self, tmp_path) -> None:
        """Basic smoke test for load_state."""
        from transcribe import load_state

        state_file = tmp_path / "state.json"
        state_file.write_text("{}")

        import transcribe

        orig = transcribe.STATE_FILE
        try:
            transcribe.STATE_FILE = state_file
            state = load_state()
            assert isinstance(state, dict)
        finally:
            transcribe.STATE_FILE = orig

    def test_get_model_url_basic(self) -> None:
        """Basic smoke test for get_model_url."""
        from transcribe import get_model_url

        url = get_model_url("base")
        assert url.startswith("https://")
        assert "base.bin" in url

    def test_find_audio_files_basic(self, tmp_path) -> None:
        """Basic smoke test for find_audio_files."""
        from transcribe import find_audio_files

        inp = tmp_path / "input"
        inp.mkdir()
        (inp / "test.mp3").write_bytes(b"fake")

        found = find_audio_files(inp)
        assert len(found) == 1

    def test_needs_transcode_basic(self) -> None:
        """Basic smoke test for needs_transcode."""
        from pathlib import Path

        from transcribe import needs_transcode

        assert needs_transcode(Path("test.mp3")) is True
        assert needs_transcode(Path("test.wav")) is False

    def test_has_audio_stream_basic(self, monkeypatch) -> None:
        """Basic smoke test for has_audio_stream."""
        import subprocess
        from pathlib import Path

        import transcribe
        from transcribe import has_audio_stream

        def fake_run(cmd, **kwargs):
            return subprocess.CompletedProcess(cmd, 0, stdout="audio", stderr="")

        monkeypatch.setattr(transcribe.subprocess, "run", fake_run)

        result = has_audio_stream(Path("/fake/test.mp3"))
        assert isinstance(result, bool)
