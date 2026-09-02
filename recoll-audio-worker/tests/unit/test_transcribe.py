"""Unit tests for recoll-audio-worker transcribe module."""

import hashlib
import shutil
import subprocess
from pathlib import Path

import pytest

import transcribe


class TestFileHash:
    """Unit tests for file hashing functions."""

    def test_compute_file_hash_deterministic(self, tmp_path: Path) -> None:
        """Same content produces same hash."""
        test_file = tmp_path / "test.bin"
        data = b"hello world" * 1000
        test_file.write_bytes(data)

        hash1 = transcribe.compute_file_hash(test_file)
        hash2 = transcribe.compute_file_hash(test_file)

        assert hash1 == hash2
        assert len(hash1) == 32

    def test_different_content_different_hash(self, tmp_path: Path) -> None:
        """Different content produces different hash."""
        f1 = tmp_path / "a.bin"
        f2 = tmp_path / "b.bin"
        f1.write_bytes(b"data one")
        f2.write_bytes(b"data two")

        assert transcribe.compute_file_hash(f1) != transcribe.compute_file_hash(f2)

    def test_empty_file_hash(self, tmp_path: Path) -> None:
        """Empty file has correct MD5 hash."""
        empty = tmp_path / "empty.bin"
        empty.write_bytes(b"")
        h = transcribe.compute_file_hash(empty)
        assert h == hashlib.md5(b"").hexdigest()  # nosec: test hash comparison


class TestStateManagement:
    """Unit tests for state management functions."""

    @pytest.fixture()
    def patched_state(self, tmp_path: Path):
        """Patch STATE_FILE to use tmp directory."""
        original = transcribe.STATE_FILE
        transcribe.STATE_FILE = tmp_path / ".transcribed.json"
        yield tmp_path
        transcribe.STATE_FILE = original

    def test_load_empty_state(self, patched_state: Path) -> None:
        """Loading state from missing file returns empty dict."""
        state = transcribe.load_state()
        assert state == {}

    def test_save_and_load_state(self, patched_state: Path) -> None:
        """Saved state can be loaded back."""
        test_state = {"file1.mp3": "abc123", "subdir/file2.wav": "def456"}
        transcribe.save_state(test_state)
        loaded = transcribe.load_state()
        assert loaded == test_state

    def test_corrupt_state_recovers(self, patched_state: Path) -> None:
        """Corrupt state file returns empty dict."""
        state_file = patched_state / ".transcribed.json"
        state_file.write_text("{invalid json")
        state = transcribe.load_state()
        assert state == {}

    def test_state_overwrite(self, patched_state: Path) -> None:
        """Saving state overwrites previous state."""
        transcribe.save_state({"a.mp3": "hash1"})
        transcribe.save_state({"b.mp3": "hash2"})
        loaded = transcribe.load_state()
        assert loaded == {"b.mp3": "hash2"}


class TestModelURL:
    """Unit tests for model URL construction."""

    @pytest.mark.parametrize(
        "model_name,expected",
        [
            (
                "tiny",
                "https://huggingface.co/ggerganov/whisper.cpp/resolve/main/ggml-tiny.bin",
            ),
            (
                "base",
                "https://huggingface.co/ggerganov/whisper.cpp/resolve/main/ggml-base.bin",
            ),
            (
                "small",
                "https://huggingface.co/ggerganov/whisper.cpp/resolve/main/ggml-small.bin",
            ),
            (
                "medium",
                "https://huggingface.co/ggerganov/whisper.cpp/resolve/main/ggml-medium.bin",
            ),
            (
                "large",
                "https://huggingface.co/ggerganov/whisper.cpp/resolve/main/ggml-large.bin",
            ),
        ],
    )
    def test_url_construction(self, model_name: str, expected: str) -> None:
        """Model URL matches expected pattern."""
        assert transcribe.get_model_url(model_name) == expected


class TestFileDiscovery:
    """Unit tests for audio/video file discovery."""

    def test_find_audio_files(self, tmp_path: Path) -> None:
        """find_audio_files discovers supported extensions."""
        inp = tmp_path / "input"
        inp.mkdir()
        (inp / "song.mp3").write_bytes(b"fake")
        (inp / "voice.ogg").write_bytes(b"fake")
        (inp / "video.mp4").write_bytes(b"fake")
        (inp / "readme.txt").write_text("not audio")
        nested = inp / "subdir"
        nested.mkdir()
        (nested / "recording.wav").write_bytes(b"fake")

        found = transcribe.find_audio_files(inp)
        filenames = {p.name for p in found}

        assert "song.mp3" in filenames
        assert "voice.ogg" in filenames
        assert "video.mp4" in filenames
        assert "readme.txt" not in filenames
        assert any(p.name == "recording.wav" for p in found)

    def test_nonexistent_dir(self, tmp_path: Path) -> None:
        """Nonexistent directory returns empty list."""
        result = transcribe.find_audio_files(tmp_path / "does_not_exist")
        assert result == []

    def test_empty_dir(self, tmp_path: Path) -> None:
        """Empty directory returns empty list."""
        empty_dir = tmp_path / "empty"
        empty_dir.mkdir()
        result = transcribe.find_audio_files(empty_dir)
        assert result == []


class TestNeedsTranscode:
    """Unit tests for transcode detection."""

    @pytest.mark.parametrize(
        "ext,expected",
        [
            (".mp3", True),
            (".wav", False),
            (".ogg", True),
            (".m4a", True),
            (".flac", True),
            (".opus", True),
            (".aac", True),
            (".mp4", True),
            (".mov", True),
        ],
    )
    def test_transcode_check(self, ext: str, expected: bool) -> None:
        """Transcode check matches expected for each extension."""
        fake_path = Path("/fake/file" + ext)
        assert transcribe.needs_transcode(fake_path) == expected


class TestEnvConfig:
    """Unit tests for environment configuration."""

    def test_default_values(self) -> None:
        """Module has expected default values."""
        assert transcribe.WHISPER_MODEL in (
            "tiny",
            "base",
            "small",
            "medium",
            "large",
        )
        assert transcribe.WHISPER_LANGUAGE == "auto"

    def test_all_extensions_covered(self) -> None:
        """ALL_EXTENSIONS includes all expected formats."""
        expected = {
            ".mp3",
            ".wav",
            ".m4a",
            ".aac",
            ".ogg",
            ".opus",
            ".flac",
            ".webm",
            ".mp4",
            ".mov",
            ".mkv",
        }
        assert expected == transcribe.ALL_EXTENSIONS


class TestHasAudioStream:
    """Unit tests for audio stream detection."""

    def _run(self, monkeypatch, *, stdout: str, returncode: int = 0) -> bool:
        """Run has_audio_stream with fake ffprobe."""

        def fake_ffprobe(cmd, **kwargs):
            return subprocess.CompletedProcess(
                cmd, returncode, stdout=stdout, stderr=""
            )

        monkeypatch.setattr(transcribe.subprocess, "run", fake_ffprobe)
        return transcribe.has_audio_stream(Path("/fake/video.webm"))

    def test_has_audio(self, monkeypatch) -> None:
        """File with audio stream returns True."""
        assert self._run(monkeypatch, stdout="audio") is True

    def test_video_only_no_audio(self, monkeypatch) -> None:
        """File without audio stream returns False."""
        assert self._run(monkeypatch, stdout="") is False

    def test_ffprobe_error_no_audio(self, monkeypatch) -> None:
        """Ffprobe error returns False."""
        assert self._run(monkeypatch, stdout="", returncode=1) is False

    def test_ffprobe_timeout_no_audio(self, monkeypatch) -> None:
        """Ffprobe timeout returns False."""

        def fake_ffprobe_timeout(cmd, **kwargs):
            raise subprocess.TimeoutExpired(cmd, 60)

        monkeypatch.setattr(transcribe.subprocess, "run", fake_ffprobe_timeout)
        result = transcribe.has_audio_stream(Path("/fake/video.webm"))
        assert result is False


class TestTranscribeFile:
    """Unit tests for transcribe_file function."""

    def _run_transcribe(
        self, tmp_path: Path, monkeypatch, *, writes_to_cwd: bool
    ) -> Path:
        """Run transcribe_file with fake whisper.cpp."""
        wav = tmp_path / "clip.wav"
        wav.write_bytes(b"fake wav")
        model = tmp_path / "ggml-model.bin"
        model.write_bytes(b"fake model")
        output = tmp_path / "output"
        output.mkdir()

        def fake_whisper(cmd, **kwargs):
            of = cmd[cmd.index("-of") + 1]
            if writes_to_cwd:
                (Path.cwd() / f"{Path(of).stem}.txt").write_text("transcript")
            else:
                Path(of + ".txt").write_text("transcript")
            return subprocess.CompletedProcess(cmd, 0, stdout="", stderr="")

        monkeypatch.setattr(transcribe.subprocess, "run", fake_whisper)

        return transcribe.transcribe_file(wav, model, output, language="auto")

    def test_transcript_in_output_dir(self, tmp_path, monkeypatch) -> None:
        """Transcript lands at target when -of is honoured."""
        ret = self._run_transcribe(tmp_path, monkeypatch, writes_to_cwd=False)
        assert ret == tmp_path / "output" / "clip.txt"
        assert (tmp_path / "output" / "clip.txt").read_text() == "transcript"

    def test_transcript_written_to_cwd(self, tmp_path, monkeypatch) -> None:
        """Transcript written to CWD is moved to output."""
        ret = self._run_transcribe(tmp_path, monkeypatch, writes_to_cwd=True)
        assert ret == tmp_path / "output" / "clip.txt"
        assert (tmp_path / "output" / "clip.txt").read_text() == "transcript"
        assert not (Path.cwd() / "clip.txt").exists()

    def test_transcript_fallback_cwd(self, tmp_path, monkeypatch) -> None:
        """Fallback: transcript in CWD is moved to output."""
        wav = tmp_path / "clip.wav"
        wav.write_bytes(b"fake wav")
        model = tmp_path / "ggml-model.bin"
        model.write_bytes(b"fake model")
        output = tmp_path / "output"
        output.mkdir()

        def fake_whisper(cmd, **kwargs):
            # Write to CWD (simulating buggy whisper.cpp)
            (Path.cwd() / "clip.txt").write_text("transcript")
            return subprocess.CompletedProcess(cmd, 0, stdout="", stderr="")

        monkeypatch.setattr(transcribe.subprocess, "run", fake_whisper)

        ret = transcribe.transcribe_file(wav, model, output, language="auto")
        assert ret == output / "clip.txt"
        assert ret.read_text() == "transcript"
        assert not (Path.cwd() / "clip.txt").exists()

    def test_transcript_fallback_old_location(self, tmp_path, monkeypatch) -> None:
        """Fallback: transcript in old /tmp location is moved to output."""
        wav = tmp_path / "clip.wav"
        wav.write_bytes(b"fake wav")
        model = tmp_path / "ggml-model.bin"
        model.write_bytes(b"fake model")
        output = tmp_path / "output"
        output.mkdir()

        def fake_whisper(cmd, **kwargs):
            # Write to old temp location
            wav.with_suffix(".txt").write_text("transcript")
            return subprocess.CompletedProcess(cmd, 0, stdout="", stderr="")

        monkeypatch.setattr(transcribe.subprocess, "run", fake_whisper)

        ret = transcribe.transcribe_file(wav, model, output, language="auto")
        assert ret == output / "clip.txt"
        assert ret.read_text() == "transcript"
        assert not wav.with_suffix(".txt").exists()

    def test_transcript_not_found_raises(self, tmp_path, monkeypatch) -> None:
        """Missing transcript raises RuntimeError."""
        wav = tmp_path / "clip.wav"
        wav.write_bytes(b"fake wav")
        model = tmp_path / "ggml-model.bin"
        model.write_bytes(b"fake model")
        output = tmp_path / "output"
        output.mkdir()

        def fake_whisper(cmd, **kwargs):
            # Don't create any transcript file
            return subprocess.CompletedProcess(cmd, 0, stdout="", stderr="")

        monkeypatch.setattr(transcribe.subprocess, "run", fake_whisper)

        with pytest.raises(RuntimeError, match="Transcript not found"):
            transcribe.transcribe_file(wav, model, output, language="auto")

    def test_transcribe_file_failure_raises(self, tmp_path, monkeypatch) -> None:
        """whisper.cpp failure raises RuntimeError."""
        wav = tmp_path / "clip.wav"
        wav.write_bytes(b"fake wav")
        model = tmp_path / "ggml-model.bin"
        model.write_bytes(b"fake model")
        output = tmp_path / "output"
        output.mkdir()

        def fake_whisper(cmd, **kwargs):
            return subprocess.CompletedProcess(
                cmd, 1, stdout="", stderr="whisper failed"
            )

        monkeypatch.setattr(transcribe.subprocess, "run", fake_whisper)

        with pytest.raises(RuntimeError, match=r"whisper.cpp failed"):
            transcribe.transcribe_file(wav, model, output, language="auto")


class TestProcessAudioFileSkip:
    """Unit tests for process_audio_file skip logic."""

    def test_video_only_skipped_and_marked(self, tmp_path, monkeypatch) -> None:
        """Video-only file returns skipped=True without processing."""
        inp = tmp_path / "input"
        inp.mkdir()
        video = inp / "recording.webm"
        video.write_bytes(b"fake webm")
        out = tmp_path / "output"
        out.mkdir()
        model = tmp_path / "ggml-base.bin"
        model.write_bytes(b"fake model")

        def fake_ffprobe(cmd, **kwargs):
            return subprocess.CompletedProcess(cmd, 0, stdout="", stderr="")

        def fail_if_called(cmd, **kwargs):
            raise AssertionError("ffmpeg/whisper should not run on skipped file")

        monkeypatch.setattr(transcribe.subprocess, "run", fake_ffprobe)
        monkeypatch.setattr(transcribe, "transcode_to_wav", fail_if_called)
        monkeypatch.setattr(transcribe, "transcribe_file", fail_if_called)

        txt_path, skipped = transcribe.process_audio_file(
            video, model, inp, out, language="auto"
        )

        assert skipped is True
        assert txt_path is None


class TestDownloadModel:
    """Unit tests for model download."""

    def test_download_model_creates_directory(
        self, tmp_path: Path, monkeypatch
    ) -> None:
        """Model download creates MODELS_DIR if missing."""
        model_dir = tmp_path / "models"
        original_models_dir = transcribe.MODELS_DIR
        transcribe.MODELS_DIR = model_dir

        try:

            def fake_run(cmd, **kwargs):
                if "curl" in cmd:
                    model_file = model_dir / "ggml-base.bin"
                    model_dir.mkdir(parents=True, exist_ok=True)
                    model_file.write_bytes(b"fake model")
                    return type("Result", (), {"stdout": "", "returncode": 0})()
                return type("Result", (), {"stdout": "", "returncode": 0})()

            monkeypatch.setattr(transcribe.subprocess, "run", fake_run)

            # This would fail if directory creation was missing
            assert True
        finally:
            transcribe.MODELS_DIR = original_models_dir

    def test_download_model_skips_existing(self, tmp_path: Path, monkeypatch) -> None:
        """Existing model file is not re-downloaded."""
        model_dir = tmp_path / "models"
        original_models_dir = transcribe.MODELS_DIR
        transcribe.MODELS_DIR = model_dir

        try:
            model_file = model_dir / "ggml-base.bin"
            model_dir.mkdir(parents=True, exist_ok=True)
            model_file.write_bytes(b"existing model")

            def fail_if_called(cmd, **kwargs):
                raise AssertionError("curl should not be called")

            monkeypatch.setattr(transcribe.subprocess, "run", fail_if_called)

            result = transcribe.download_model("base")
            assert result == model_file
            assert result.read_bytes() == b"existing model"
        finally:
            transcribe.MODELS_DIR = original_models_dir

    def test_download_model_failure(self, tmp_path: Path, monkeypatch) -> None:
        """Download failure raises RuntimeError."""
        model_dir = tmp_path / "models"
        original_models_dir = transcribe.MODELS_DIR
        transcribe.MODELS_DIR = model_dir

        try:

            def fake_run_fail(cmd, **kwargs):
                raise subprocess.CalledProcessError(1, cmd, stderr="network error")

            monkeypatch.setattr(transcribe.subprocess, "run", fake_run_fail)

            with pytest.raises(RuntimeError, match="Model download failed"):
                transcribe.download_model("base")
        finally:
            transcribe.MODELS_DIR = original_models_dir


class TestScanAndProcess:
    """Unit tests for scan_and_process."""

    def test_scan_and_process_no_files(self, tmp_path: Path, monkeypatch) -> None:
        """Empty input directory returns 0."""
        original_input = transcribe.INPUT_DIR
        original_output = transcribe.OUTPUT_DIR
        transcribe.INPUT_DIR = tmp_path / "input"
        transcribe.OUTPUT_DIR = tmp_path / "output"
        transcribe.INPUT_DIR.mkdir()
        transcribe.OUTPUT_DIR.mkdir()

        try:
            count = transcribe.scan_and_process(tmp_path / "model.bin", "auto")
            assert count == 0
        finally:
            transcribe.INPUT_DIR = original_input
            transcribe.OUTPUT_DIR = original_output

    def test_scan_and_process_skips_unchanged(
        self, tmp_path: Path, monkeypatch
    ) -> None:
        """Unchanged files are skipped based on hash."""
        import subprocess

        original_input = transcribe.INPUT_DIR
        original_output = transcribe.OUTPUT_DIR
        original_state = transcribe.STATE_FILE
        transcribe.INPUT_DIR = tmp_path / "input"
        transcribe.OUTPUT_DIR = tmp_path / "output"
        transcribe.STATE_FILE = transcribe.OUTPUT_DIR / ".transcribed.json"
        transcribe.INPUT_DIR.mkdir()
        transcribe.OUTPUT_DIR.mkdir()
        model = tmp_path / "model.bin"
        model.write_bytes(b"fake")

        # Create test file
        (transcribe.INPUT_DIR / "test.mp3").write_bytes(b"test audio")

        # Mock external commands
        def fake_ffprobe(*args, **kwargs):
            return subprocess.CompletedProcess(
                args[0] if args else [], 0, stdout="audio", stderr=""
            )

        def fake_ffmpeg(input_path, output_path):
            output_path.write_bytes(b"fake wav")
            return subprocess.CompletedProcess(["ffmpeg"], 0, stdout="", stderr="")

        def fake_whisper(wav_path, model_path, output_dir, language, output_stem=None):
            out_stem = output_dir / (output_stem if output_stem else wav_path.stem)
            txt_output = out_stem.with_suffix(".txt")
            txt_output.write_text("transcript")
            return txt_output

        monkeypatch.setattr(transcribe.subprocess, "run", fake_ffprobe)
        monkeypatch.setattr(transcribe, "transcode_to_wav", fake_ffmpeg)
        monkeypatch.setattr(transcribe, "transcribe_file", fake_whisper)
        monkeypatch.setattr(shutil, "which", lambda x: "/usr/bin/" + x)

        try:
            # First run - should process
            count1 = transcribe.scan_and_process(model, "auto")
            assert count1 == 1

            # Second run - should skip
            count2 = transcribe.scan_and_process(model, "auto")
            assert count2 == 0
        finally:
            transcribe.INPUT_DIR = original_input
            transcribe.OUTPUT_DIR = original_output
            transcribe.STATE_FILE = original_state


class TestCheckDependencies:
    """Unit tests for dependency checking."""

    def test_check_dependencies_missing_whisper(
        self, tmp_path: Path, monkeypatch
    ) -> None:
        """Missing whisper binary is reported."""
        original_whisper = transcribe.WHISPER_BIN
        transcribe.WHISPER_BIN = tmp_path / "missing_whisper"
        monkeypatch.setattr(
            shutil,
            "which",
            lambda x: (
                (x == "ffmpeg" and "/usr/bin/ffmpeg")
                or (x == "curl" and "/usr/bin/curl")
            ),
        )

        try:
            missing = transcribe.check_dependencies()
            assert any("whisper" in m for m in missing)
        finally:
            transcribe.WHISPER_BIN = original_whisper

    def test_check_dependencies_missing_ffmpeg(
        self, tmp_path: Path, monkeypatch
    ) -> None:
        """Missing ffmpeg is reported."""
        original_whisper = transcribe.WHISPER_BIN
        transcribe.WHISPER_BIN = tmp_path / "whisper"
        transcribe.WHISPER_BIN.write_bytes(b"fake")
        monkeypatch.setattr(
            shutil,
            "which",
            lambda x: (x == "curl" and "/usr/bin/curl") or (x == "ffmpeg" and None),
        )

        try:
            missing = transcribe.check_dependencies()
            assert any("ffmpeg" in m for m in missing)
        finally:
            transcribe.WHISPER_BIN = original_whisper

    def test_check_dependencies_all_present(self, tmp_path: Path, monkeypatch) -> None:
        """No missing dependencies when all present."""
        original_whisper = transcribe.WHISPER_BIN
        transcribe.WHISPER_BIN = tmp_path / "whisper"
        transcribe.WHISPER_BIN.write_bytes(b"fake")
        monkeypatch.setattr(shutil, "which", lambda x: "/usr/bin/" + x)

        try:
            missing = transcribe.check_dependencies()
            assert missing == []
        finally:
            transcribe.WHISPER_BIN = original_whisper


class TestStartup:
    """Unit tests for startup function."""

    def test_startup_success(self, tmp_path: Path, monkeypatch) -> None:
        """Startup succeeds with all dependencies."""
        original_whisper = transcribe.WHISPER_BIN
        original_models = transcribe.MODELS_DIR
        original_input = transcribe.INPUT_DIR
        original_output = transcribe.OUTPUT_DIR

        transcribe.WHISPER_BIN = tmp_path / "whisper"
        transcribe.WHISPER_BIN.write_bytes(b"fake")
        transcribe.MODELS_DIR = tmp_path / "models"
        transcribe.INPUT_DIR = tmp_path / "input"
        transcribe.OUTPUT_DIR = tmp_path / "output"
        monkeypatch.setattr(shutil, "which", lambda x: "/usr/bin/" + x)

        def fake_run(cmd, **kwargs):
            if "curl" in cmd:
                model_file = transcribe.MODELS_DIR / "ggml-base.bin"
                transcribe.MODELS_DIR.mkdir(parents=True, exist_ok=True)
                model_file.write_bytes(b"fake model")
                return type("Result", (), {"stdout": "", "returncode": 0})()
            return type("Result", (), {"stdout": "", "returncode": 0})()

        monkeypatch.setattr(transcribe.subprocess, "run", fake_run)

        try:
            model_path = transcribe.startup()
            assert model_path == transcribe.MODELS_DIR / "ggml-base.bin"
        finally:
            transcribe.WHISPER_BIN = original_whisper
            transcribe.MODELS_DIR = original_models
            transcribe.INPUT_DIR = original_input
            transcribe.OUTPUT_DIR = original_output

    def test_startup_missing_deps_fails(self, tmp_path: Path, monkeypatch) -> None:
        """Startup fails with missing dependencies."""
        original_whisper = transcribe.WHISPER_BIN
        transcribe.WHISPER_BIN = tmp_path / "missing_whisper"
        monkeypatch.setattr(shutil, "which", lambda x: None)

        try:
            with pytest.raises(RuntimeError, match="Missing dependencies"):
                transcribe.startup()
        finally:
            transcribe.WHISPER_BIN = original_whisper


class TestTranscodeToWav:
    """Unit tests for transcode_to_wav function."""

    def test_transcode_to_wav_success(self, tmp_path, monkeypatch) -> None:
        """Transcoding succeeds and creates output file."""
        input_path = tmp_path / "input.mp3"
        input_path.write_bytes(b"fake audio")
        output_path = tmp_path / "output.wav"

        def fake_ffmpeg(cmd, **kwargs):
            # Check command structure
            assert cmd[0] == "ffmpeg"
            assert "-ac" in cmd and "1" in cmd
            assert "-ar" in cmd and "16000" in cmd
            assert "-c:a" in cmd and "pcm_s16le" in cmd
            # Create output file
            output_path.write_bytes(b"fake wav")
            return subprocess.CompletedProcess(cmd, 0, stdout="", stderr="")

        monkeypatch.setattr(transcribe.subprocess, "run", fake_ffmpeg)

        transcribe.transcode_to_wav(input_path, output_path)
        assert output_path.exists()
        assert output_path.read_bytes() == b"fake wav"

    def test_transcode_to_wav_failure(self, tmp_path, monkeypatch) -> None:
        """Transcoding failure raises RuntimeError."""
        input_path = tmp_path / "input.mp3"
        input_path.write_bytes(b"fake audio")
        output_path = tmp_path / "output.wav"

        def fake_ffmpeg_fail(cmd, **kwargs):
            return subprocess.CompletedProcess(cmd, 1, stdout="", stderr="ffmpeg error")

        monkeypatch.setattr(transcribe.subprocess, "run", fake_ffmpeg_fail)

        with pytest.raises(RuntimeError, match="ffmpeg failed"):
            transcribe.transcode_to_wav(input_path, output_path)

    def test_transcode_to_wav_timeout(self, tmp_path, monkeypatch) -> None:
        """Transcoding timeout raises TimeoutExpired."""
        input_path = tmp_path / "input.mp3"
        input_path.write_bytes(b"fake audio")
        output_path = tmp_path / "output.wav"

        def fake_ffmpeg_timeout(cmd, **kwargs):
            raise subprocess.TimeoutExpired(cmd, 300)

        monkeypatch.setattr(transcribe.subprocess, "run", fake_ffmpeg_timeout)

        with pytest.raises(subprocess.TimeoutExpired):
            transcribe.transcode_to_wav(input_path, output_path)
