"""Audio/Video Transcription Worker
=================================
Polls input directories for audio and video files, transcribes them to
plain-text transcripts using whisper.cpp, and writes .txt sidecars to
the output directory.

Input:  /input/<source>/**/*.<ext>  (audio/video files)
Output: /output/<source>/<path>/<filename>.txt

State (processed files) persisted to /output/.transcribed.json so
restarts don't re-process everything.

Pipeline:
    audio file → ffmpeg (normalize to WAV mono 16kHz) → whisper.cpp → .txt transcript

Supported extensions:
    Audio:  mp3, wav, m4a, aac, ogg, opus, flac
    Video:  webm, mp4, mov, mkv
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s  %(levelname)-8s  %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
log = logging.getLogger("audio-worker")

# ---------------------------------------------------------------------------
# Configurable via env vars
# ---------------------------------------------------------------------------
INPUT_DIR = Path(os.environ.get("INPUT_DIR", "/input"))
OUTPUT_DIR = Path(os.environ.get("OUTPUT_DIR", "/output"))
POLL_SECONDS = int(os.environ.get("POLL_INTERVAL", "300"))
WHISPER_MODEL = os.environ.get("WHISPER_MODEL", "base")
WHISPER_LANGUAGE = os.environ.get("WHISPER_LANGUAGE", "auto")
STATE_FILE = OUTPUT_DIR / ".transcribed.json"
MODELS_DIR = Path("/models")

# whisper.cpp CLI binary path
WHISPER_BIN = Path("/usr/local/bin/whisper")

# HuggingFace URL pattern for model downloads
MODEL_URL_BASE = (
    "https://huggingface.co/ggerganov/whisper.cpp/resolve/main/ggml-{model}.bin"
)

# Audio and video extensions to process
AUDIO_EXTENSIONS = {".mp3", ".wav", ".m4a", ".aac", ".ogg", ".opus", ".flac"}
VIDEO_EXTENSIONS = {".webm", ".mp4", ".mov", ".mkv"}
ALL_EXTENSIONS = AUDIO_EXTENSIONS | VIDEO_EXTENSIONS


# ---------------------------------------------------------------------------
# State management — track which files we've already processed
# ---------------------------------------------------------------------------
def load_state() -> dict[str, str]:
    """Return {relative_path: md5} of already-processed files."""
    if STATE_FILE.exists():
        try:
            return json.loads(STATE_FILE.read_text())
        except (json.JSONDecodeError, OSError):
            log.warning("Corrupt state file, starting fresh")
            return {}
    return {}


def save_state(state: dict[str, str]) -> None:
    """Persist state dict to JSON file."""
    STATE_FILE.write_text(json.dumps(state, indent=2))


def file_hash(path: Path) -> str:
    """Compute MD5 hash of a file."""
    h = hashlib.md5()
    # Read in chunks for large files
    for chunk in iter(lambda: path.read_bytes()[len(h.hexdigest()) :], b""):
        h.update(chunk)
    return h.hexdigest()


def compute_file_hash(path: Path) -> str:
    """Compute MD5 hash of a file, reading in chunks to handle large files."""
    h = hashlib.md5()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(8192), b""):
            h.update(chunk)
    return h.hexdigest()


# ---------------------------------------------------------------------------
# Model download
# ---------------------------------------------------------------------------
def get_model_url(model_name: str) -> str:
    """Return the HuggingFace URL for a whisper.cpp model."""
    return MODEL_URL_BASE.format(model=model_name)


def download_model(model_name: str) -> Path:
    """Download the whisper.cpp ggml model if not already present.

    Returns the path to the downloaded model file.
    Raises RuntimeError if download fails.
    """
    model_file = MODELS_DIR / f"ggml-{model_name}.bin"

    if model_file.exists() and model_file.stat().st_size > 0:
        log.info("Model already present: %s", model_file)
        return model_file

    url = get_model_url(model_name)
    log.info("Downloading model '%s' from %s", model_name, url)

    MODELS_DIR.mkdir(parents=True, exist_ok=True)

    # Use curl to download with retry logic
    cmd = [
        "curl",
        "--fail",
        "--retry",
        "3",
        "--retry-delay",
        "5",
        "-L",
        url,
        "-o",
        str(model_file),
    ]

    try:
        result = subprocess.run(
            cmd, check=True, capture_output=True, text=True, timeout=600
        )
        log.info(
            "Download output: %s", result.stdout[-200:] if result.stdout else "(none)"
        )
    except subprocess.CalledProcessError as e:
        raise RuntimeError(f"Model download failed: {e.stderr}") from e
    except subprocess.TimeoutExpired as e:
        raise RuntimeError("Model download timed out after 600s") from e

    if not model_file.exists() or model_file.stat().st_size == 0:
        raise RuntimeError(f"Model file is empty after download: {model_file}")

    log.info(
        "Model downloaded: %s (%d bytes)",
        model_file,
        model_file.stat().st_size,
    )
    return model_file


# ---------------------------------------------------------------------------
# Audio processing
# ---------------------------------------------------------------------------
def find_audio_files(base_dir: Path) -> list[Path]:
    """Recursively find audio and video files in base_dir."""
    if not base_dir.exists():
        log.warning("Input directory %s does not exist", base_dir)
        return []

    found: list[Path] = []
    for root, _dirs, files in os.walk(base_dir):
        for fname in sorted(files):
            ext = Path(fname).suffix.lower()
            if ext in ALL_EXTENSIONS:
                found.append(Path(root) / fname)
    return sorted(found)


def needs_transcode(audio_path: Path) -> bool:
    """Check if the file needs transcoding to WAV mono 16kHz.

    Returns True if the file is not already a PCM WAV mono 16kHz.
    For simplicity, we always transcode non-WAV files.
    """
    return audio_path.suffix.lower() != ".wav"


def has_audio_stream(path: Path) -> bool:
    """Return True if the media file contains an audio stream.

    Used to skip video-only files (e.g. screen recordings without a mic),
    which ffmpeg cannot convert to WAV ("Output file #0 does not contain
    any stream"). Non-zero exit or empty output -> no audio stream.
    """
    cmd = [
        "ffprobe",
        "-v",
        "error",
        "-select_streams",
        "a:0",
        "-show_entries",
        "stream=codec_type",
        "-of",
        "csv=p=0",
        str(path),
    ]
    try:
        result = subprocess.run(
            cmd, capture_output=True, text=True, timeout=60, check=False
        )
    except subprocess.TimeoutExpired:
        log.warning("ffprobe timed out probing %s — assuming no audio stream", path)
        return False
    return result.stdout.strip() == "audio"


def transcode_to_wav(input_path: Path, output_path: Path) -> None:
    """Transcode audio to WAV mono 16kHz using ffmpeg."""
    cmd = [
        "ffmpeg",
        "-y",
        "-i",
        str(input_path),
        "-ac",
        "1",
        "-ar",
        "16000",
        "-c:a",
        "pcm_s16le",
        str(output_path),
    ]

    log.debug("Transcoding: %s → %s", input_path, output_path)
    result = subprocess.run(
        cmd, capture_output=True, text=True, timeout=300, check=False
    )
    if result.returncode != 0:
        log.error("ffmpeg failed for %s: %s", input_path, result.stderr[-200:])
        raise RuntimeError(f"ffmpeg failed: {result.stderr[-200:]}")


def transcribe_file(
    wav_path: Path,
    model_path: Path,
    output_dir: Path,
    language: str,
    output_stem: str | None = None,
) -> Path:
    """Transcribe a WAV file using whisper.cpp CLI.

    Returns the path to the generated .txt file.
    """
    # Use explicit output_stem if provided (e.g., original filename without _transcode suffix),
    # otherwise fall back to the WAV path's stem.
    stem = output_stem if output_stem is not None else wav_path.stem
    out_stem = output_dir / stem  # absolute, no .txt extension
    txt_output = out_stem.with_suffix(".txt")

    cmd = [
        str(WHISPER_BIN),
        "-m",
        str(model_path),
        "-f",
        str(wav_path),
        "-otxt",
        "-l",
        language,
        "-of",
        str(out_stem),  # absolute output filename stem
        "-od",
        str(output_dir),
    ]

    log.info("Transcribing: %s → %s", wav_path, txt_output)
    result = subprocess.run(
        cmd, capture_output=True, text=True, timeout=600, check=False
    )

    if result.returncode != 0:
        log.error("whisper.cpp failed for %s: %s", wav_path, result.stderr[-300:])
        raise RuntimeError(f"whisper.cpp failed: {result.stderr[-300:]}")

    # Verify output exists — whisper.cpp honours an absolute -of, but on
    # some versions only writes <stem>.txt next to the process CWD (/app).
    if not txt_output.exists():
        for candidate in (
            Path.cwd() / f"{wav_path.stem}.txt",
            wav_path.with_suffix(".txt"),  # old /tmp location
        ):
            if candidate.exists():
                shutil.move(str(candidate), str(txt_output))
                break
        else:
            raise RuntimeError(
                f"Transcript not found at {txt_output} "
                f"(checked CWD {Path.cwd()} and {wav_path})"
            )

    log.info("Transcript written: %s (%d bytes)", txt_output, txt_output.stat().st_size)
    return txt_output


# ---------------------------------------------------------------------------
# Main processing logic
# ---------------------------------------------------------------------------
def process_audio_file(
    audio_path: Path,
    model_path: Path,
    input_base: Path,
    output_base: Path,
    language: str,
) -> tuple[Path | None, bool]:
    """Process a single audio file.

    Transcodes to WAV if needed, runs whisper.cpp, writes .txt to output.
    Returns (transcript_path, skipped): transcript_path is the .txt file, or
    None on failure. `skipped` is True when the file was intentionally left
    untranscribed (no audio stream) and should be marked processed in state.
    """
    # Mirror directory structure in output
    rel_path = audio_path.relative_to(input_base)
    out_subdir = output_base / rel_path.parent
    out_subdir.mkdir(parents=True, exist_ok=True)

    tmp_wav = Path(tempfile.gettempdir()) / f"{audio_path.stem}_transcode.wav"

    try:
        # Video-only files (no audio stream) can't be transcribed — skip them
        # so ffmpeg doesn't fail on "Output file #0 does not contain any stream".
        if needs_transcode(audio_path) and not has_audio_stream(audio_path):
            log.info("No audio stream in %s — skipping", audio_path)
            return None, True

        # Transcode if needed
        if needs_transcode(audio_path):
            transcode_to_wav(audio_path, tmp_wav)
            wav_input = tmp_wav
        else:
            wav_input = audio_path

        # Transcribe - pass original audio filename stem
        # so transcript uses correct name
        txt_path = transcribe_file(
            wav_input, model_path, out_subdir, language, audio_path.stem
        )

        return txt_path, False

    except (RuntimeError, OSError) as e:
        log.error("Failed to process %s: %s", audio_path, e)
        return None, False

    finally:
        # Cleanup temp file
        if tmp_wav.exists():
            tmp_wav.unlink()


def scan_and_process(model_path: Path, language: str) -> int:
    """Scan INPUT_DIR for new/changed audio files and process them."""
    state = load_state()
    processed_count = 0

    audio_files = find_audio_files(INPUT_DIR)
    if not audio_files:
        return 0

    log.debug("Found %d audio files in total", len(audio_files))

    for audio_path in audio_files:
        rel = str(audio_path.relative_to(INPUT_DIR))
        current_hash = compute_file_hash(audio_path)
        saved_hash = state.get(rel)

        if saved_hash == current_hash:
            continue

        log.info("Processing new/changed file: %s", rel)

        txt_path, skipped = process_audio_file(
            audio_path, model_path, INPUT_DIR, OUTPUT_DIR, language
        )

        # Record in state on success OR intentional skip (e.g. video-only file
        # with no audio stream) so we don't retry the same file every poll.
        if txt_path is not None or skipped:
            state[rel] = current_hash
            processed_count += 1

    if processed_count:
        save_state(state)

    return processed_count


# ---------------------------------------------------------------------------
# Startup checks
# ---------------------------------------------------------------------------
def check_dependencies() -> list[str]:
    """Check required dependencies and return list of missing ones."""
    missing = []

    if not WHISPER_BIN.exists():
        missing.append(f"whisper.cpp binary: {WHISPER_BIN}")

    if shutil.which("ffmpeg") is None:
        missing.append("ffmpeg")

    if shutil.which("curl") is None:
        missing.append("curl")

    return missing


def startup() -> Path:
    """Run startup checks and download model. Returns model path."""
    log.info("=" * 60)
    log.info("Audio/Video Transcription Worker")
    log.info("=" * 60)

    # Check dependencies
    missing = check_dependencies()
    if missing:
        raise RuntimeError(f"Missing dependencies: {', '.join(missing)}")

    log.info("Config:")
    log.info("  Input:       %s", INPUT_DIR)
    log.info("  Output:      %s", OUTPUT_DIR)
    log.info("  Poll:        every %ds", POLL_SECONDS)
    log.info("  Model:       %s", WHISPER_MODEL)
    log.info("  Language:    %s", WHISPER_LANGUAGE)
    log.info("=" * 60)

    # Create directories
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    MODELS_DIR.mkdir(parents=True, exist_ok=True)

    # Download model
    return download_model(WHISPER_MODEL)


# ---------------------------------------------------------------------------
# Main loop
# ---------------------------------------------------------------------------
def main() -> None:
    """Entry point for the transcription worker."""
    try:
        model_path = startup()
    except RuntimeError as e:
        log.error("Startup failed: %s", e)
        sys.exit(1)

    # Initial run
    count = scan_and_process(model_path, WHISPER_LANGUAGE)
    if count:
        log.info("Initial run: processed %d file(s)", count)
    else:
        log.info("Initial run: no new files to process")

    # Poll loop
    iteration = 0
    while True:
        time.sleep(POLL_SECONDS)
        iteration += 1
        try:
            count = scan_and_process(model_path, WHISPER_LANGUAGE)
            if count:
                log.info("Poll #%d: processed %d new file(s)", iteration, count)
            else:
                log.debug("Poll #%d: no changes", iteration)
        except Exception:
            log.exception("Poll #%d encountered an error", iteration)


if __name__ == "__main__":
    main()
