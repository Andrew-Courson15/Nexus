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
  `~/Library/Application Support/NEXUS`). Built and checked on every push on real Windows and Mac machines
  (GitHub Actions: the app starts, captures and searches; on Windows the installer installs,
  runs and uninstalls, keeping the archive). About 150 MB, or about 490 MB with voice-note transcription included.
  The first-run check links to Ollama and downloads the AI model in one click. On Windows the build
  script also makes `NEXUS-Setup-<version>.exe` when Inno Setup is installed (`packaging/nexus.iss`:
  per-user install, no admin prompt, Start menu, optional desktop icon and start at sign-in; the
  uninstaller keeps your ideas). The Mac build
  makes a `.dmg` too. Still to do: signing (Windows code-signing certificate; Apple Developer ID +
  notarization), so people aren't warned away when they open it.
- **Previously:** setup needed Python, Git and a terminal.
  - A Windows installer (`.exe`/`.msi`) and a Mac app (`.dmg`) would bundle Python and NEXUS,
    install or guide Ollama, and start NEXUS at login.
  - Tools: PyInstaller or Briefcase.
  - **Code signing:** an Apple Developer ID (about $99/year) and a Windows signing
    certificate. Without them, both systems warn users away.
- **Updates without Git.** Done for the Windows app (0.15.1): Update now downloads the new
  installer from the Releases page (GitHub addresses only, size and file type checked), installs
  it silently over the running copy and opens NEXUS again; CI tests that path. The Mac app offers
  the download for now. Still to do: signed downloads, and self-update on the Mac. A folder copy
  of NEXUS still updates with git.
- **Simpler phone setup.** Done in 0.14: Settings → Your phone guides through Tailscale and turns
  on access with one button, then shows a QR code. Tailscale is still a separate app and account;
  a later option is a paid relay service.
- **Model management in the app.** Done: download and switch models in Preferences with a progress
  bar, with a recommendation based on the computer's memory (0.14). Still to do: removing models,
  and taking the graphics card into account.
- **Testing on real hardware.** Measure speed and quality on several Windows PCs (NVIDIA / AMD
  / none) and Macs (8 / 16 / 24 GB), and tune the default models per machine.
- **Accessibility.** Done in 0.14: contrast in both themes (checked with axe), screen-reader
  names, keyboard focus, reduced motion, and easier tapping on the map. axe reports no issues on any page.
  Still to do: a full screen-reader walkthrough (VoiceOver, NVDA).
- **Crash reporting you control.** Started in 0.14: Settings → Something not working? shows a
  report to copy. A future version could offer to send it, with the same preview.
- **Documentation.** 0.14 has an in-app Help page for everyday use. Still to do: split this README
  into a user guide (website) and a developer guide.
- **Legal pages.** `PRIVACY.md` (0.14) says what's stored where and what NEXUS connects to. Still
  to do: terms of use, and a license (your decision, above).

## iPhone

- `ios/` is a native iPhone app (see IOS.md), built and run in the simulator on every push. For
  the App Store it needs an Apple Developer account, an app record, a hosted privacy policy, and
  a demo NEXUS for App Review.

## Nice next features

- ~~A timeline view of the archive.~~ Done in 0.14.
- ~~Characters, places and worlds as their own pages.~~ Done in 0.14.
- Page-by-page analysis of a whole comic (pacing and visual consistency, from the pages
  themselves). 0.14 can already split a comic PDF into page entries, which is the first step.
- Importing ebooks into the art library.
- Optional cloud AI (OpenAI or Anthropic), only if the user turns it on, with a clear warning.
