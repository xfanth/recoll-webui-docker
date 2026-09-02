"""Integration tests for recoll-audio-worker - test cross-component data flow."""

import subprocess
import tempfile
from pathlib import Path

import transcribe


class TestIntegration:
    """Integration tests for end-to-end processing pipeline."""

    def setup_method(self) -> None:
        """Set up test directories."""
        self.temp_dir = tempfile.TemporaryDirectory()
        self.tmp = Path(self.temp_dir.name)

        self.input_dir = self.tmp / "input"
        self.output_dir = self.tmp / "output"
        self.input_dir.mkdir()
        self.output_dir.mkdir()

        # Override module constants
        self.orig_input = transcribe.INPUT_DIR
        self.orig_output = transcribe.OUTPUT_DIR
        self.orig_state = transcribe.STATE_FILE
        self.orig_models = transcribe.MODELS_DIR

        transcribe.INPUT_DIR = self.input_dir
        transcribe.OUTPUT_DIR = self.output_dir
        transcribe.STATE_FILE = self.output_dir / ".transcribed.json"
        transcribe.MODELS_DIR = self.tmp / "models"

    def teardown_method(self) -> None:
        """Restore original module constants."""
        transcribe.INPUT_DIR = self.orig_input
        transcribe.OUTPUT_DIR = self.orig_output
        transcribe.STATE_FILE = self.orig_state
        transcribe.MODELS_DIR = self.orig_models
        self.temp_dir.cleanup()

    def test_find_audio_files_then_hash(self) -> None:
        """Integration: file discovery feeds into hashing."""
        # Create test files
        (self.input_dir / "song.mp3").write_bytes(b"fake audio data")
        (self.input_dir / "voice.wav").write_bytes(b"fake wav data")
        nested = self.input_dir / "subdir"
        nested.mkdir()
        (nested / "recording.ogg").write_bytes(b"fake ogg data")

        # Discover files
        found = transcribe.find_audio_files(self.input_dir)
        assert len(found) == 3

        # Hash each discovered file
        hashes = {
            str(p.relative_to(self.input_dir)): transcribe.compute_file_hash(p)
            for p in found
        }
        assert len(hashes) == 3
        assert all(len(h) == 32 for h in hashes.values())

    def test_state_persistence_across_runs(self) -> None:
        """Integration: state file persists across function calls."""
        # Create test file
        audio = self.input_dir / "test.mp3"
        audio.write_bytes(b"test data")

        # Compute hash and save to state
        h = transcribe.compute_file_hash(audio)
        state = {str(audio.relative_to(self.input_dir)): h}
        transcribe.save_state(state)

        # Simulate new process - load state
        loaded = transcribe.load_state()
        assert loaded == state

    def test_model_url_to_download_flow(self, monkeypatch) -> None:
        """Integration: model URL generation works with download logic."""
        # Test URL construction
        url = transcribe.get_model_url("base")
        assert "base.bin" in url
        assert url.startswith("https://")

    def test_transcode_decision_flow(self) -> None:
        """Integration: needs_transcode correctly routes files."""
        # WAV files don't need transcode
        wav_file = Path("/test/file.wav")
        assert transcribe.needs_transcode(wav_file) is False

        # MP3 files need transcode
        mp3_file = Path("/test/file.mp3")
        assert transcribe.needs_transcode(mp3_file) is True

        # Video files need transcode
        mp4_file = Path("/test/file.mp4")
        assert transcribe.needs_transcode(mp4_file) is True

    def test_audio_stream_detection_integration(self, monkeypatch) -> None:
        """Integration: has_audio_stream feeds into process_audio_file skip logic."""
        inp = self.input_dir
        video = inp / "silent.webm"
        video.write_bytes(b"fake")
        out = self.output_dir
        model = self.tmp / "model.bin"
        model.write_bytes(b"fake")

        # ffprobe reports no audio
        def fake_ffprobe(*args, **kwargs):
            return subprocess.CompletedProcess(
                args[0] if args else [], 0, stdout="", stderr=""
            )

        def fail_if_called(*args, **kwargs):
            raise AssertionError("Should not transcode video with no audio")

        monkeypatch.setattr(transcribe.subprocess, "run", fake_ffprobe)
        monkeypatch.setattr(transcribe, "transcode_to_wav", fail_if_called)
        monkeypatch.setattr(transcribe, "transcribe_file", fail_if_called)

        # Video-only file should be skipped
        txt_path, skipped = transcribe.process_audio_file(
            video, model, inp, out, language="auto"
        )

        assert skipped is True
        assert txt_path is None

    def test_full_transcription_pipeline_mock(self, tmp_path, monkeypatch) -> None:
        """Integration: full mock pipeline from file to transcript."""
        # Setup
        inp = tmp_path / "input"
        inp.mkdir()
        audio = inp / "test.mp3"
        audio.write_bytes(b"fake audio")
        out = tmp_path / "output"
        out.mkdir()
        model = tmp_path / "model.bin"
        model.write_bytes(b"fake model")

        # Mock all external commands
        def fake_ffprobe(*args, **kwargs):
            return subprocess.CompletedProcess(
                args[0] if args else [], 0, stdout="audio", stderr=""
            )

        def fake_ffmpeg(*args, **kwargs):
            # args[0] is input path, args[1] is output path
            out_wav = args[1] if len(args) > 1 else Path("/tmp/fake.wav")
            out_wav.write_bytes(b"fake wav")
            return subprocess.CompletedProcess(["ffmpeg"], 0, stdout="", stderr="")
        
        # Also mock has_audio_stream to return True
        monkeypatch.setattr(transcribe, "has_audio_stream", lambda x: True)

        def fake_whisper(wav_path, model_path, output_dir, language, output_stem=None):
            out_stem = output_dir / (output_stem if output_stem else wav_path.stem)
            txt_output = out_stem.with_suffix(".txt")
            txt_output.write_text("This is the transcript")
            return txt_output

        monkeypatch.setattr(transcribe.subprocess, "run", fake_ffprobe)
        monkeypatch.setattr(transcribe, "transcode_to_wav", fake_ffmpeg)
        monkeypatch.setattr(transcribe, "transcribe_file", fake_whisper)

        # Run full pipeline
        txt_path, skipped = transcribe.process_audio_file(
            audio, model, inp, out, language="auto"
        )

        assert skipped is False
        assert txt_path is not None
        assert txt_path.name == "test.txt"
        assert txt_path.read_text() == "This is the transcript"
