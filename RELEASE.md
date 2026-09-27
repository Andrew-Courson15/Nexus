# Toward a public release

NEXUS works well as a personal tool today. This page lists what a public release would still
need, so the work can be planned. Items marked **(your decision)** are choices only the owner
can make; the rest is engineering.

## Already in place

- **Privacy by design:**
  - everything runs on the user's computer, with local AI (Ollama) and local speech-to-text (Whisper)
  - no accounts and no cloud
  - phone access stays private through Tailscale, with an optional passcode
- **Trust:**
  - original material is never changed
  - AI output is always labeled
  - every edit is kept as a version you can restore
  - backups, a readable Markdown export, and a trash with restore (plus delete forever)
- **Reliability:**
  - about 200 automated tests
  - browser tests for the phone, offline mode, sharing and the passcode
  - safe database upgrades on start
  - a one-command update
- **Everyday use without a terminal:** desktop icons, in-app preferences, and a first-run setup check.

## Decisions for the owner

1. **License (your decision).** Open source (MIT, Apache-2.0, GPL) or closed / paid. The
   choice affects the rest of this list. One dependency to be aware of: PDF/SVG previews use
   PyMuPDF, which is AGPL (or needs a paid license for closed-source distribution). It could be
   replaced (for example with pypdfium2 + resvg) if you choose a closed or permissive license.
2. **Name and branding (your decision).** Check that "NEXUS" is free to use as a product name
   in your market. It's a common word in software.
3. **Who it's for (your decision).** Solo creators (as today), or also teams sharing one
   archive, which would need user accounts and per-person attribution.
4. **Business model (your decision).** Free, one-time purchase, or subscription. With no cloud
   there are no server costs, which keeps "free" or "one-time" realistic.

## Engineering still needed for non-technical users

- **Real installers.** Started in 0.14: `scripts/build_app_windows.ps1` and `scripts/build_app_mac.sh`
  build a standalone app with PyInstaller (`packaging/nexus.spec`) that runs without Python or Git.
  Unzip and double-click; data goes to the per-user app folder (`%LOCALAPPDATA%\NEXUS`,
  `~/Library/Application Support/NEXUS`). Tested as a Linux build here (starts in about a second;
  pictures, PDFs, search and the phone QR work); the Windows and Mac builds still need a first
  run on real machines. About 150 MB, or about 490 MB with voice-note transcription included.
  The first-run check links to Ollama and downloads the AI model in one click. On Windows the build
  script also makes `NEXUS-Setup-<version>.exe` when Inno Setup is installed (`packaging/nexus.iss`:
  per-user install, no admin prompt, Start menu, optional desktop icon and start at sign-in; the
  uninstaller keeps your ideas). Still to do: a `.dmg` on Mac, first runs on real machines, and signing.
- **Previously:** setup needed Python, Git and a terminal.
  - A Windows installer (`.exe`/`.msi`) and a Mac app (`.dmg`) would bundle Python and NEXUS,
    install or guide Ollama, and start NEXUS at login.
  - Tools: PyInstaller or Briefcase.
  - **Code signing:** an Apple Developer ID (about $99/year) and a Windows signing
    certificate. Without them, both systems warn users away.
- **Updates without Git.** 0.14 has an in-app "Check for updates / Update now" (Settings →
  Updates), still using git underneath. A public release would swap git for a signed download.
- **Simpler phone setup.** Tailscale works well but is a separate app and account. Options:
  guide it inside NEXUS (show a QR code of the address), or later a paid relay service.
- **Model management in the app.** Download, switch and remove Ollama models from Preferences
  with a progress bar, and recommend a model based on the computer's memory and graphics card.
- **Testing on real hardware.** Measure speed and quality on several Windows PCs (NVIDIA / AMD
  / none) and Macs (8 / 16 / 24 GB), and tune the default models per machine.
- **Accessibility.** Done in 0.14: contrast in both themes (checked with axe), screen-reader
  names, keyboard focus, reduced motion, and easier tapping on the map. axe reports no issues on any page.
  Still to do: a full screen-reader walkthrough (VoiceOver, NVDA).
- **Crash reporting you control.** Started in 0.14: Settings → Something not working? shows a
  report to copy. A future version could offer to send it, with the same preview.
- **Documentation.** Split this README into a user guide (website/help pages) and a developer guide.
- **Legal pages.** A privacy policy (short, since nothing leaves the device) and terms of use.

## Nice next features

- ~~A timeline view of the archive.~~ Done in 0.14.
- ~~Characters, places and worlds as their own pages.~~ Done in 0.14.
- Page-by-page analysis of a whole comic (pacing and visual consistency, from the pages
  themselves). 0.14 can already split a comic PDF into page entries, which is the first step.
- Importing ebooks into the art library.
- Optional cloud AI (OpenAI or Anthropic), only if the user turns it on, with a clear warning.
