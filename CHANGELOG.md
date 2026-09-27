# Changelog

The version is in `app/__init__.py` and shown in **Settings → This device**.

## 0.13.0 (2026-09-27)

- **Double-click installers**: `Install NEXUS (Windows).bat` and `Install NEXUS (Mac).command`
  install what's missing (Python, Ollama; Homebrew on a Mac), choose a lighter model on
  low-memory computers, set NEXUS up with its icon, offer phone setup, and open it.
- **Settings → Your phone**: the phone's private address as a QR code to scan.
- **Preferences → Download a model**, with a progress bar (no terminal needed).

## 0.12.0 (2026-09-27)

**Phone**
- **Fixed:** on the very first visit after an update, the copy the phone had just saved could be
  dropped from memory. That session then showed "NEXUS isn't reachable" instead of your archive
  once the computer was off.
- **Settings → This device** shows:
  - the NEXUS version
  - whether a copy is saved on this device, and when
  - how many entries, projects and pictures it holds
  - why saving failed, if it did
  - an **Update the saved copy now** button
- Phones reload into a newer NEXUS by themselves, when opened and when you come back to the app.
- One unusual record can no longer stop the whole copy from being saved.

**Updating**
- One command stops every running NEXUS, downloads the update, sets it up and starts it again:
  `scripts\update_windows.ps1` and `scripts/update_mac.sh`.

**New**
- **Preferences** in Settings:
  - AI model and model for pictures (picked from what's installed)
  - speech-to-text model and voice-note language
  - search strictness and automatic picture analysis
  - backup folder, schedule and how many to keep
  - your name
- **Passcode** for phones and other devices. The computer NEXUS runs on never asks for it.
  Includes a sign-out-all button and rate-limited guessing.
- **Select several entries** in the Archive: add to project, set status, add tag, export, trash.
- **Tags page**: rename (merges into an existing tag) and delete.
- **Readable export**: the whole archive as Markdown files plus every original file.
- **Long voice notes and texts** are read in parts, instead of being cut off at about 12,000 characters.
- **Delete forever** and **Empty trash** (only for things already in the Trash).
- **Duplicate notice** when you upload a file you've already saved (both are kept).
- **First-run welcome** with a setup check, and **keyboard shortcuts**.
- Phone setup for a NEXUS on a Mac: `scripts/phone_setup_mac.sh`.

## 0.10.0

- **Connections map**: your ideas and their links, laid out so related ideas cluster.
- **Link ideas yourself** from an entry ("character in", "evolved from" …), and remove links.
- **Project analysis**: characters, story threads, world, strongest ideas, contradictions, open
  questions and gaps across a whole project, each citing its entries. Every version is kept.
- **Share with a friend's NEXUS**: export entries or projects as `.nexus` files and import them,
  with attribution ("from Sebas"), fingerprint checks and no duplicates.
- **Ask NEXUS**: questions answered only from your archive, with links to the entries used.
- **Backups**: automatic and on demand, one zip with the database and every original file,
  with a safe restore.
- **Phone offline copy**: browse entries, projects, art and search while the computer is off.
- Small **thumbnails** for cards and grids.

## 0.5.0

- **Many more picture formats**: TIFF, PSD, TGA, WebP, BMP, HEIC, SVG, PDF, Illustrator,
  Procreate, Krita and more. The original is kept; a preview is made for viewing and for the AI.
- **Stricter search by meaning**, and fewer, better related-idea suggestions.
- **Desktop icons** for Windows and Mac.

## 0.4.0

- **Visual Art Intelligence**:
  - analysis and critique modes
  - an artist mode that talks with you about a piece
  - comic page, sequence and storyboard reading
  - comparison, character consistency and evolution
  - creative memory and an art library
- **Phone app** (installable) with private access through Tailscale, offline capture, and no more
  black screen when the computer is off.

## 0.3.0 and earlier

- Search by meaning, a trash view, restoring earlier versions, and projects in the trash.
- Local AI through Ollama, local Whisper transcription, image understanding.
- Windows setup and start scripts.
- Milestone 1: capture, the creative archive, the idea incubator, projects, search, revision
  history, and the USER SAID / AI INTERPRETATION / AI INFERENCE / UNKNOWN labels.
