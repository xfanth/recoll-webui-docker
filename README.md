# Unified Search Infrastructure

One-stop search for your entire digital life — documents, photos, emails, audio, and messages.

## Architecture

![Architecture](docs/architecture.svg)

A Dockerized homelab stack that indexes all personal data into searchable stores:

| Service | Status | What It Does |
|---------|--------|-------------|
| **Recoll Engine** | ✅ Running | Full-text index for documents (PDF, DOCX, ODT), images (EXIF/IPTC/XMP via exiftool), scanned PDFs (Tesseract OCR), and email (Maildir) |
| **Recoll WebUI** | ✅ Running | Bottle-based web search interface (port 9080) |
| **Immich** | ✅ Running | Photo/video library with ML-powered face recognition and CLIP tags (port 2283) |
| **Email (mbsync)** | ✅ Running | IMAP → Maildir sync every 5 minutes (no web UI) |
| **WhatsApp Archiver** | ✅ Running | Baileys multi-device client that continuously exports chats to plaintext and downloads media |
| **SMS Processor** | ✅ Running | Parses SMS Backup & Restore XML exports into per-contact markdown files |
| **Audio Worker** | 🟡 WIP | faster-whisper container ready; transcription pipeline and Recoll integration pending |

## Data Flow

![Data Flow](docs/data-flow.svg)

Each service follows the same pattern: **source → processor → indexed output → search**.

- **Email**: IMAP → mbsync → Maildir → Recoll index
- **Documents**: Google Drive/Syncthing mount → Recoll (OCR + metadata extraction) → xapian index
- **Photos/Videos**: Google Photos mount → Immich (PostgreSQL + ML) → Immich UI
- **WhatsApp**: WhatsApp Web API → Baileys archiver → `.txt` chat files + media → Recoll index
- **SMS**: SMS Backup & Restore XML → Python processor → per-contact `.md` → Recoll index
- **Audio** (planned): Audio files → ffmpeg normalize → Whisper transcription → `.txt` → Recoll index

## Roadmap

![Roadmap](docs/roadmap.svg)

### Phase 1 ✅ — Documents, Images & Email
- [x] Recoll indexing for documents (PDF, DOCX, ODT, XLSX, PPTX, TXT, RTF, EPUB)
- [x] Image metadata extraction (EXIF, IPTC, XMP via exiftool)
- [x] OCR for scanned PDFs (Tesseract English)
- [x] Recoll WebUI
- [x] Immich integration (photos, videos, ML tags, face recognition)
- [x] Email sync (mbsync IMAP → Maildir every 5 minutes)

### Messages ✅ — WhatsApp & SMS
- [x] WhatsApp Archiver (Baileys multi-device client)
- [x] Continuous message export to plain-text (one file per contact)
- [x] Media download (images, audio, video, documents in dated folders)
- [x] Multi-account support with session persistence
- [x] Exponential backoff with jitter on reconnect
- [x] Non-root container
- [x] SMS Processor (XML backup → per-contact markdown)
- [x] State tracking (`processed.json`) to avoid reprocessing

### Phase 2 🟡 — Audio
- [ ] Whisper transcription pipeline (faster-whisper container ready)
- [ ] Audio worker integration with Recoll indexing
- [ ] Transcribed text indexed by Recoll
- [ ] Multi-language support
- [ ] Speaker diarization

### Phase 3 🔮 — Unified UI
- [ ] Single search box querying all backends simultaneously
- [ ] WhatsApp message search (data is ready, needs UI integration)
- [ ] SMS message search (data is ready, needs UI integration)
- [ ] Semantic search across all sources (CLIP embeddings)
- [ ] Cross-source result ranking

### Hardening 🔒
- [x] Exit code bug fixed (`recollindex.py`)
- [x] `.dockerignore` files for all services
- [x] LibreOffice temp dir sticky bit
- [x] mbsync password security (no process exposure)
- [x] Paths parameterized via environment variables
- [x] WhatsApp exponential backoff
- [x] WhatsApp non-root container
- [ ] WebUI authentication (`httpPassword` in recoll.conf)
- [ ] Recoll-engine non-root migration
- [ ] Python 3 / Ubuntu 24.04 migration (Recoll WebUI uses Python 2 + bottle 0.10)

## Quick Start

```bash
# 1. Copy environment file and set secure passwords
cp .env.example .env
# Edit .env with secure passwords

# 2. Pull images
docker compose pull

# 3. Start everything
docker compose up -d

# 4. Check status
docker compose ps
```

## Services

| Service | Port | URL | Purpose |
|---------|------|-----|---------|
| recoll-webui | 9080 | http://localhost:9080 | Document/image/email search |
| immich-server | 2283 | http://localhost:2283 | Photo/video library |
| mbsync | (none) | — | Background IMAP → Maildir sync (no web UI)

## Data Layout

### Host → Container mounts (Recoll)

| Host Path | Container Path | Content |
|-----------|----------------|---------|
| syncthing/alex-hades-home | /homes/alex/hades | Alex's home files |
| syncthing/alex-phone | /homes/alex/phone | Alex's phone backup |
| alex-home/google-drive | /homes/alex/gdrive | Alex's Google Drive |
| alex-home/google-photos | /homes/alex/gphotos | Alex's Google Photos |
| syncthing/chloe-home | /homes/chloe/home | Chloe's home files |
| syncthing/chloe-phone | /homes/chloe/phone | Chloe's phone backup |
| chloe-home/google-drive | /homes/chloe/gdrive | Chloe's Google Drive |
| chloe-home/google-photos | /homes/chloe/gphotos | Chloe's Google Photos |
| whatsapp/data | /data (whatsapp-alex / -chloe) | WhatsApp chat exports + media |
| sms/input | /input (sms-processor) | SMS Backup & Restore XML files |
| sms/output | /output (sms-processor) | Organized markdown output |

### Index storage

- Recoll index: `/mnt/shuttle/share/app-data/recoll`
- Immich data: `/mnt/shuttle/share/app-data/immich`
- mbsync config: `/mnt/shuttle/share/app-data/mbsync/config`
- mbsync Maildir: `/mnt/shuttle/share/app-data/mbsync/data`
- WhatsApp config: `/mnt/shuttle/share/app-data/whatsapp/config`
- WhatsApp exports: `/mnt/shuttle/share/app-data/whatsapp/data`

## Configuration

Recoll is configured via [`recoll.conf`](recoll.conf). It covers:
- **Documents**: PDF (with OCR fallback), DOCX, ODT, XLSX, PPTX, TXT, RTF, EPUB
- **Images**: JPEG, PNG, TIFF, GIF — EXIF/IPTC/XMP metadata via exiftool
- **Audio**: MP3, M4A, OGG, FLAC — metadata via ffmpeg (transcription coming in Phase 2)
- **OCR**: Tesseract English for scanned PDFs

### Environment variables

Path constants are defined as YAML anchors in `docker-compose.yml` (`x-constants`). Edit once, reuse everywhere via `*alias`.

The Python wrapper respects `RECOLL_BASE_PATH` (default: `/mnt/shuttle/share`).

## Directory Structure

```
.
├── docker-compose.yml           # Unified compose (all services)
├── .env.example                 # Environment variable template
├── .pre-commit-config.yaml      # Pre-commit lint + pre-push Docker builds
├── recoll.conf                  # Recoll indexing configuration
├── Dockerfile                   # Recoll WebUI container
├── docs/                        # Animated SVG diagrams
│   ├── architecture.svg         # System architecture
│   ├── data-flow.svg            # Data pipeline flow
│   └── roadmap.svg              # Implementation roadmap
├── mbsync/
│   └── mbsyncrc                 # IMAP → Maildir sync configuration
├── recoll-engine/
│   ├── Dockerfile               # Recoll indexer (ubuntu 22.04 + recoll)
│   └── README.md
├── recoll-webui/                # Recoll web interface source (bottle)
├── recoll-audio-worker/
│   ├── Dockerfile               # faster-whisper transcription container
│   └── README.md
├── recoll_wrapper/              # Python wrapper for Recoll indexing
│   ├── pyproject.toml
│   └── recollindex.py           # Index orchestrator with progress bars
├── sms-processor/
│   ├── Dockerfile
│   ├── process.py               # XML → markdown processor
│   └── pyproject.toml
└── whatsapp-archiver/
    ├── Dockerfile               # node:20-slim, non-root
    ├── index.js                 # Baileys archiver entry point
    ├── lib.js                   # Archiver utilities
    └── README.md
```

## Service Details

### WhatsApp Archiver

Two containers (`whatsapp-alex`, `whatsapp-chloe`) each connect as a WhatsApp Web multi-device client via [@whiskeysockets/baileys](https://github.com/WhiskeySockets/Baileys). Messages are appended to per-contact `.txt` files; media is downloaded to dated folders. Sessions persist across restarts so you only scan the QR code once per account.

See [whatsapp-archiver/README.md](whatsapp-archiver/README.md) for multi-account setup.

### SMS Processor

Reads SMS Backup & Restore XML files from a mounted input directory and produces per-contact markdown files. Tracks processed files in `processed.json` so restarts don't reprocess everything. Runs on a configurable poll interval.

### Recoll Engine

Custom Ubuntu 22.04 container with Recoll, poppler-utils (PDF text extraction), Tesseract OCR, exiftool, and LibreOffice (document conversion). Indexes all mounted data directories. Controlled by `recoll_wrapper/recollindex.py` for incremental reindexing with progress bars.

### Immich

Self-hosted photo/video management. Uses PostgreSQL + Redis. ML backend handles face recognition and CLIP-based semantic search.

## CI / CD

GitHub Actions workflow (`.github/workflows/ci.yml`):
1. **Lint**: Ruff, shellcheck, hadolint, yamllint
2. **Test**: pytest for recoll_wrapper
3. **Docker build**: Build all images on PR ( gated on CI passing)

Pre-commit hooks run linting; pre-push hooks parallelize Docker builds.

## Deployment Rule: Remote-only stack
**This docker-compose stack runs on truenas.arpa (remote TrueNAS), NOT locally.**
- Only test containers run locally (use local Docker for testing/verification)
- Do NOT try to run docker commands against the remote host — no remote Docker access
- Paths in docker-compose.yml and .env are TrueNAS paths (/mnt/shuttle/share/...)
- Do NOT inspect local Docker for production state — it only shows test containers
- **All logs, errors, and container state you share are from PRODUCTION on TrueNAS — local Docker does NOT have them**

## Solutions & Known Issues

See [SOLUTIONS.md](SOLUTIONS.md) for the full adversarial review of 19 findings, including accepted fixes, rejected items, and phased implementation plans.

## TrueNAS Notes

This compose file originated from TrueNAS app exports. Standard Docker users can run it directly. TrueNAS-specific init containers (permissions, postgres_upgrade) are removed — set ownership on the host instead.

## Immich Existing Data (standalone stack on TrueNAS)
The standalone Immich on TrueNAS uses these separate paths:
- PostgreSQL: `/mnt/shuttle/share/app-data/immich/pg_data`
- Server uploads: `/mnt/shuttle/share/app-data/immich/data`
- ML cache: `/mnt/shuttle/share/app-data/immich/cache`
- Redis: named volume `redis-data` (managed by Docker)

The current docker-compose.yml needs to be updated to mount these correctly.
# CI test update 2026-08-08T13:00:05Z

## Folder Path Filtering Fix

The original Recoll WebUI folder dropdown returned relative paths by stripping the parent directory from each entry. Recoll’s `dir:` query clause requires absolute paths (e.g., `dir:/home/user/docs`). Consequently, selecting a folder in the UI produced no results.

The fix modifies `recoll-webui/webui.py::get_dirs` to return full absolute paths and updates `recoll-webui/views/search.tpl` so the UI displays only the folder name while using the absolute path as the `<option>` value. A new test (`tests/test_get_dirs.py`) verifies that `get_dirs` now returns absolute paths and includes the `<all>` entry.

This change restores proper folder‑path filtering and aligns the UI with Recoll documentation.
