# Changelog

The version is in `app/__init__.py` and shown in **Settings → This device**.

## 0.14.0 (2026-09-27)

- **A downloadable app, first step**: `scripts/build_app_windows.ps1` / `scripts/build_app_mac.sh` build
  NEXUS as a standalone app (no Python or Git needed). See RELEASE.md.
- **Update from inside NEXUS**: Settings → Updates checks for a newer version, lists what's new,
  and updates in one click on the computer NEXUS runs on. The page reloads by itself afterwards.
- **Rediscover** on Today: ideas from this day in earlier months or years, and a few unfinished
  ideas you haven't touched for a month.
- **Timeline**: the whole archive month by month, as picture tiles (Creative Archive → Timeline).
- **Characters** and **Places & worlds**: each character or place with the sketches and scenes
  linked to it and the entries that mention its name, for all projects or one. Also on the phone
  when the PC is off.
- **Arrange a project**: put its entries in your own order (story or page order).
- **Phone copy refreshes a few seconds after any change** made from the phone, instead of up to 5
  minutes later.
- **Start a project from an entry**.
- **Story bible**: print a project (or save it as a PDF), grouped into characters, places, story,
  art and notes, with your words and pictures.
- **Split a PDF into pages**: a comic issue or scanned sketchbook becomes one picture entry per
  page, linked to the PDF, with previous/next page links, and can then be read as a comic
  sequence six pages at a time. The PDF is never changed.
- **Phone setup inside NEXUS**: Settings → Your phone walks through Tailscale and turns on private
  phone access with one button, then shows the QR code. No script or restart needed. It also has
  **Start NEXUS when I sign in** (Windows and Mac).
- **Phone**: keeps up to 2,000 pictures for when the PC is off (was 500). Settings → This device
  shows how many are saved.
- **Uploading many files at once** shows its progress, and can date each picture by the file's
  own date, so older work lands in the right month on the Timeline.
- **Search: "Did you mean …?"** when nothing matched because of a typo ("villian" → villain),
  using the words in your own archive.
- **Search** finds pictures and files by their file name ("el_faro_pose.png" → *faro*), leaving
  out camera names like IMG_2041. Existing entries are re-indexed once, in about a second.
- **Duplicates page** (Creative Archive → Duplicates): identical files grouped, extra copies moved to
  the Trash in one click (restorable). Copies with your own words are marked.
- **Archive filter by kind**: pictures, voice notes, written, files.
- **Preferences recommend an AI model** that fits this computer's memory.
- **First-run check** links to Ollama when it's missing and downloads the AI model in one click.
- **Help** (Settings → Help, or press `h`): how NEXUS works and fixes for common problems. Also
  works on the phone when the computer is off.
- **What's new** (this list) shows once on Home after an update.
- **Theme** per device (Settings → This device): same as the device, dark, or light.
- **Tag suggestions** while typing a tag.
- **Develop this idea**: directions, questions and a next step from the local AI, labelled as AI
  inference and kept with the entry.
- **More like this** on an entry: the entries closest in meaning (needs search by meaning).
- **Art gallery filter**: find artworks by title, tag or file name, by project, or those not analysed
  yet, and **analyse all the ones not analysed yet** in one go (they run one after another).
- **Previous / Next** on an entry opened from a grid (Archive, Timeline, a project, Art…): buttons,
  arrow keys, or swiping the picture on a phone. Tap a picture to see it full screen.
- **Map**: tapping just beside an idea opens it, which is much easier on a phone.
- **Settings → Something not working?**: a problem report to paste when asking for help. You
  see it first and copy it yourself; nothing is sent.
- **Accessibility**: readable contrast for small grey text and buttons in light and dark mode,
  clearer keyboard focus, screen-reader names, and calmer motion when the system asks for it.

## 0.13.0 (2026-09-27)

- **System tray icon on Windows**: NEXUS shows an icon at the bottom right while it runs. Left-click
  opens it; right-click → **Quit NEXUS** turns it off. It also starts this way with Windows.
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
