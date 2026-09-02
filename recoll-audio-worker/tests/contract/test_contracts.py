"""Contract tests for recoll-audio-worker - verify API contracts and data schemas."""

import subprocess
from pathlib import Path

import transcribe


class TestTranscribeContract:
    """Contract tests for transcribe module - verify function signatures and return types."""

    def test_compute_file_hash_returns_32_char_hex(self, tmp_path: Path) -> None:
        """compute_file_hash must return 32-char hex string."""
        test_file = tmp_path / "test.bin"
        test_file.write_bytes(b"test data")

        h = transcribe.compute_file_hash(test_file)

        assert isinstance(h, str)
        assert len(h) == 32
        assert all(c in "0123456789abcdef" for c in h)

    def test_load_state_returns_dict(self, tmp_path: Path) -> None:
        """load_state must return dict[str, str]."""
        original_state = transcribe.STATE_FILE
        transcribe.STATE_FILE = tmp_path / ".transcribed.json"

        try:
            state = transcribe.load_state()
            assert isinstance(state, dict)
        finally:
            transcribe.STATE_FILE = original_state

    def test_save_state_accepts_dict(self, tmp_path: Path) -> None:
        """save_state must accept dict[str, str]."""
        original_state = transcribe.STATE_FILE
        transcribe.STATE_FILE = tmp_path / ".transcribed.json"

        try:
            transcribe.save_state({"file.mp3": "abc123"})
            loaded = transcribe.load_state()
            assert loaded == {"file.mp3": "abc123"}
        finally:
            transcribe.STATE_FILE = original_state

    def test_get_model_url_returns_valid_url(self) -> None:
        """get_model_url must return valid HTTPS URL for known models."""
        for model in ["tiny", "base", "small", "medium", "large"]:
            url = transcribe.get_model_url(model)
            assert isinstance(url, str)
            assert url.startswith("https://")
            assert "huggingface.co" in url
            assert f"ggml-{model}.bin" in url

    def test_find_audio_files_returns_list_of_paths(self, tmp_path: Path) -> None:
        """find_audio_files must return list[Path]."""
        inp = tmp_path / "input"
        inp.mkdir()
        (inp / "test.mp3").write_bytes(b"fake")

        result = transcribe.find_audio_files(inp)

        assert isinstance(result, list)
        assert all(isinstance(p, Path) for p in result)

    def test_needs_transcode_returns_bool(self) -> None:
        """needs_transcode must return bool."""
        for ext in [".mp3", ".wav", ".ogg", ".mp4"]:
            result = transcribe.needs_transcode(Path(f"/fake/file{ext}"))
            assert isinstance(result, bool)

    def test_has_audio_stream_returns_bool(self, monkeypatch) -> None:
        """has_audio_stream must return bool."""

        def fake_run(cmd, **kwargs):
            return subprocess.CompletedProcess(cmd, 0, stdout="audio", stderr="")

        monkeypatch.setattr(transcribe.subprocess, "run", fake_run)

        result = transcribe.has_audio_stream(Path("/fake/test.mp3"))
        assert isinstance(result, bool)

    def test_transcribe_file_returns_path(self, tmp_path, monkeypatch) -> None:
        """transcribe_file must return Path to transcript."""
        wav = tmp_path / "clip.wav"
        wav.write_bytes(b"fake")
        model = tmp_path / "model.bin"
        model.write_bytes(b"fake")
        output = tmp_path / "output"
        output.mkdir()

        def fake_whisper(cmd, **kwargs):
            of = cmd[cmd.index("-of") + 1]
            Path(of + ".txt").write_text("transcript")
            return subprocess.CompletedProcess(cmd, 0, stdout="", stderr="")

        monkeypatch.setattr(transcribe.subprocess, "run", fake_whisper)

        result = transcribe.transcribe_file(wav, model, output, language="auto")
        assert isinstance(result, Path)
        assert result.suffix == ".txt"


class TestConfigContract:
    """Contract tests for configuration constants."""

    def test_whisper_model_in_known_models(self) -> None:
        """WHISPER_MODEL must be one of known models."""
        known = {"tiny", "base", "small", "medium", "large"}
        assert transcribe.WHISPER_MODEL in known

    def test_whisper_language_is_string(self) -> None:
        """WHISPER_LANGUAGE must be string."""
        assert isinstance(transcribe.WHISPER_LANGUAGE, str)

    def test_poll_seconds_is_positive_int(self) -> None:
        """POLL_SECONDS must be positive integer."""
        assert isinstance(transcribe.POLL_SECONDS, int)
        assert transcribe.POLL_SECONDS > 0

    def test_all_extensions_covers_audio_and_video(self) -> None:
        """ALL_EXTENSIONS must include both audio and video formats."""
        audio = {".mp3", ".wav", ".m4a", ".aac", ".ogg", ".opus", ".flac"}
        video = {".webm", ".mp4", ".mov", ".mkv"}

        for ext in audio | video:
            assert ext in transcribe.ALL_EXTENSIONS


class TestProcessAudioFileContract:
    """Contract tests for process_audio_file return values."""

    def test_returns_tuple_of_path_and_bool(self, tmp_path, monkeypatch) -> None:
        """process_audio_file must return (Path|None, bool)."""
        inp = tmp_path / "input"
        inp.mkdir()
        audio = inp / "test.wav"
        audio.write_bytes(b"fake")
        out = tmp_path / "output"
        out.mkdir()
        model = tmp_path / "model.bin"
        model.write_bytes(b"fake")

        def fake_whisper(wav_path, model_path, output_dir, language, output_stem=None):
            out_stem = output_dir / (output_stem if output_stem else wav_path.stem)
            txt_output = out_stem.with_suffix(".txt")
            txt_output.write_text("transcript")
            return txt_output

        def fake_ffprobe(*args, **kwargs):
            return subprocess.CompletedProcess(
                args[0] if args else [], 0, stdout="audio", stderr=""
            )

        monkeypatch.setattr(transcribe.subprocess, "run", fake_ffprobe)
        monkeypatch.setattr(transcribe, "transcribe_file", fake_whisper)

        result = transcribe.process_audio_file(audio, model, inp, out, "auto")

        assert isinstance(result, tuple)
        assert len(result) == 2
        assert result[0] is None or isinstance(result[0], Path)
        assert isinstance(result[1], bool)
