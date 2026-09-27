# Privacy

NEXUS is built so that your ideas stay yours. In short: **everything runs on your own computer,
there are no accounts, and nothing is sent anywhere unless you choose to.**

## What NEXUS stores, and where

- **Your archive** (your words, recordings, pictures and files, the AI's notes, and every earlier
  version) is kept on the computer NEXUS runs on: in the `data` folder next to NEXUS, or, for
  the downloadable app, in your user folder (`%LOCALAPPDATA%\NEXUS` on Windows,
  `~/Library/Application Support/NEXUS` on a Mac).
- **Your phone** keeps a read-only copy of your archive (and small versions of your pictures) in
  the phone's own browser storage, so you can look through it when the computer is off. Ideas you
  capture while the computer is off wait on the phone until they're sent.
- **Backups** are zip files in the folder you choose (Settings → Backups).
- **Settings** live in `.env` and `data/preferences.json`. API keys are never stored by NEXUS.

## What NEXUS connects to

- **The AI (Ollama) and speech-to-text (Whisper)** run on your computer. Your material is not
  sent to any AI company. (Ollama downloads a model once when you ask it to, from Ollama's site.)
- **Your phone** reaches NEXUS through **Tailscale**, a private encrypted network that only your
  own devices can join. NEXUS is never exposed to the public internet. Tailscale sees which of
  your devices connect, not what's in your archive.
- **Checking for updates** (only when you press the button, from the source-folder version) asks
  the NEXUS code repository whether there's a newer version.
- **Nothing else.** No analytics, no tracking, no crash reports sent automatically. The problem
  report in Settings is only shown to you; you decide whether to copy it anywhere.

## Sharing

Exporting an entry or a project makes a `.nexus` file. It goes only where you send it. It
contains your words and files for those entries and your name if you set one; it doesn't include
the AI's notes or anything else from your archive.

## Deleting

Moving something to the Trash hides it; **Delete forever** removes it and its files from NEXUS.
Older backups may still contain it until they're replaced. Uninstalling the downloadable app
keeps your archive folder, so nothing is lost by accident; delete that folder yourself to remove
everything.
