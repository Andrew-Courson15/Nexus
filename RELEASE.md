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

- **Real installers.** Today setup needs Python, Git and a terminal.
  - A Windows installer (`.exe`/`.msi`) and a Mac app (`.dmg`) would bundle Python and NEXUS,
    install or guide Ollama, and start NEXUS at login.
  - Tools: PyInstaller or Briefcase.
  - **Code signing:** an Apple Developer ID (about $99/year) and a Windows signing
    certificate. Without them, both systems warn users away.
- **Updates without Git.** Replace `git pull` with a signed download plus an in-app
  "Update available" button.
- **Simpler phone setup.** Tailscale works well but is a separate app and account. Options:
  guide it inside NEXUS (show a QR code of the address), or later a paid relay service.
- **Model management in the app.** Download, switch and remove Ollama models from Preferences
  with a progress bar, and recommend a model based on the computer's memory and graphics card.
- **Testing on real hardware.** Measure speed and quality on several Windows PCs (NVIDIA / AMD
  / none) and Macs (8 / 16 / 24 GB), and tune the default models per machine.
- **Accessibility pass.** Screen-reader labels, focus order, contrast in both themes, and
  larger tap targets on the map.
- **Crash reporting you control.** An opt-in "send a report" button that shows exactly what
  would be sent.
- **Documentation.** Split this README into a user guide (website/help pages) and a developer guide.
- **Legal pages.** A privacy policy (short, since nothing leaves the device) and terms of use.

## Nice next features

- A calendar or timeline view of the archive.
- Characters, places and worlds as their own pages, gathering every sketch and note about them.
- Page-by-page analysis of a whole comic (pacing and visual consistency, from the pages themselves).
- Importing PDFs and ebooks into the art library.
- Optional cloud AI (OpenAI or Anthropic), only if the user turns it on, with a clear warning.
