# NEXUS

A private, local-first creative knowledge archive: a personal AI operating system
for capturing thoughts, ideas, sketches, recordings, stories and research, and slowly
organizing them into a searchable body of work.

NEXUS keeps **your original material untouched** and builds a clearly labelled
AI layer around it:

| Label | Meaning | Where it lives |
|---|---|---|
| **USER SAID** | What you actually wrote, recorded or uploaded | `original_text`, `user_notes`, files on disk, your tags, your title/type choices |
| **AI INTERPRETATION** | The AI's summary and categorisation | `ai_summary`, `ai_interpretation`, concepts, AI tags |
| **AI INFERENCE** | Things the AI thinks *may* be true | `ai_inference`, themes, potential projects, suggested relationships, patterns |
| **UNKNOWN** | Not established yet | empty fields, `ai_metadata.uncertainties` |

Everything runs on your own computer. The AI is a language model running locally through
[Ollama](https://ollama.com), and voice notes are transcribed locally with Whisper. By default
nothing is sent anywhere.

**What it does, at a glance**

- **Capture anything, instantly**:
  - text, voice notes, sketches and photos in almost any picture format (PSD, TIFF, HEIC,
    Procreate …)
  - from your computer or your phone, even when the computer is off
- **Organized by a local AI**:
  - titles, types, tags, summaries and suggested links, all clearly labeled as the AI's
  - long voice logs are read in full
- **Find it again**:
  - search by words and by meaning
  - **Ask NEXUS** questions answered only from your archive, with citations
  - a **connections map**
- **Visual Art Intelligence**: critiques, artist-mode conversations, comic-page and storyboard
  reading, character consistency, and how a piece evolved.
- **Projects**, with an AI **project analysis**: characters, story threads, contradictions,
  open questions.
- **Your phone**:
  - private access from anywhere through Tailscale
  - a saved copy to browse when the computer is off
  - captures wait on the phone until NEXUS is back
  - an optional passcode
- **Yours to keep**:
  - automatic backups and a readable Markdown export
  - share entries or whole projects with a friend's NEXUS as a file
  - trash with restore, and delete forever when you mean it

See [CHANGELOG.md](CHANGELOG.md) for what's new and [RELEASE.md](RELEASE.md) for what a public
release still needs.

---

## Easiest install (double-click)

1. Get the NEXUS folder: **Code → Download ZIP** on GitHub, then unzip it. On Windows, put it in
   your user folder. On a Mac, put it in your home folder.
2. Double-click the installer for your computer:
   - **Windows:** `Install NEXUS (Windows).bat`
   - **Mac:** `Install NEXUS (Mac).command`. If the Mac says it can't be opened, right-click it and
     choose **Open**.
3. Answer the few questions and wait for the downloads.

The installer:
- installs anything missing (Python and Ollama, plus Homebrew on a Mac)
- chooses a lighter AI model on computers with less memory
- downloads the AI models (a one-time download, several GB)
- sets NEXUS up and puts a **NEXUS icon** on your desktop
- offers to set up your phone
- opens NEXUS

It's safe to run again, which also repairs a broken setup. The step-by-step instructions below do
the same by hand.

## Setup on a Mac (recommended)

**Install these once:**
- **Python 3.10 or newer.** The easiest way is `brew install python@3.12` (Homebrew: https://brew.sh).
  The `python3` that ships with macOS is too old.
- **Ollama**, from https://ollama.com/download. Open it once; afterwards it runs from the menu bar.

**Then, in Terminal:**

```bash
git clone -b claude/nexus-ai-os-rhh5si https://github.com/Andrew-Courson15/Nexus.git nexus
cd nexus
./scripts/setup_mac.sh
```

The script is safe to run again. If something is missing, it tells you exactly what. It does the following:
1. Creates a private Python environment (`.venv`).
2. Installs NEXUS and local Whisper.
3. Creates your `.env` settings file.
4. Downloads the local model `gemma3:12b` (about 8 GB) through Ollama.
5. Downloads the Whisper speech model (about 0.5 GB).

These downloads happen once. After that, NEXUS works offline.

**Start NEXUS whenever you want to use it:**

```bash
cd nexus
source .venv/bin/activate
python run.py
```

Open **http://localhost:8000**. The **Settings** page shows a green dot when Ollama and Whisper are ready.

**Or use the NEXUS icon.** The setup puts a NEXUS icon on your Desktop and in your Dock. You can
also add it at any time with `./scripts/make_mac_app.sh`, or run it with `--no-dock` to skip the
Dock.
- Click the icon to start NEXUS in the background and open it in your browser.
- Click it again while NEXUS is running to open it or stop it.
- The server log is written to `data/nexus.log`.
- If you move the `nexus` folder, run the script again.

**What to expect:**
- **Captures save instantly.** Transcription and organizing happen in the background, and the
  entry updates by itself when they finish.
- **Speed.** On an M4 with 24 GB, organizing an entry with `gemma3:12b` should take somewhere
  between several seconds and about a minute, depending on length. The first one after a start is
  slower while the model loads. For faster, lighter results, set `NEXUS_OLLAMA_MODEL=gemma3:4b` in `.env`.
- **If Ollama isn't running,** captures still work. They are organized by the simple offline
  heuristics and marked as such. Use **Re-run AI organizer** later.

## Setup on Windows

**Install these once:**
- **Python 3.12.** In PowerShell, run `winget install Python.Python.3.12`, or use the installer from
  python.org and tick **"Add python.exe to PATH"**.
- **Git:** `winget install Git.Git`
- **Ollama for Windows**, from https://ollama.com/download. It uses your NVIDIA GPU automatically.

Close PowerShell and open a new window, so it picks up the new programs. Then run:

```powershell
cd $HOME
git clone -b claude/nexus-ai-os-rhh5si https://github.com/Andrew-Courson15/Nexus.git nexus
cd nexus
powershell -ExecutionPolicy Bypass -File scripts\setup_windows.ps1
```

**To start NEXUS,** double-click the **NEXUS icon** on your desktop or in the Start menu. The setup
creates it. NEXUS starts in the background and opens http://localhost:8000.
- While NEXUS runs, its icon sits in the **system tray** (bottom right, near the clock; it may be
  under the **^** arrow). Left-click it to open NEXUS. Right-click it and choose **Quit NEXUS** to
  turn it off.
- Double-click the icon again while NEXUS is running to open it or stop it.
- The server log is in `data\nexus.log`.
- To pin it to the taskbar: open Start, right-click NEXUS, and choose **Pin to taskbar**.
- To add the icon again (for example, after moving the folder), run
  `powershell -ExecutionPolicy Bypass -File scripts\make_windows_app.ps1`.
- You can still use `scripts\start_windows.bat`, which keeps a visible window open. Close that window to stop NEXUS.

Notes for Windows:
- The setup script reports your graphics card's video memory. With less than 12 GB, use `gemma3:4b` for speed.
- Whisper runs on the CPU unless NVIDIA's CUDA libraries are installed. It switches to the CPU
  automatically if the GPU path fails. On an i5-11400, the `small` model is quick enough for voice notes.

## NEXUS on your phone (iPhone or Android)

Your phone uses the **same NEXUS that runs on your PC**, so everything is in one archive: ideas
captured on the phone appear on the PC and the other way round. The phone reaches the PC over
[Tailscale](https://tailscale.com), a free, private, encrypted network that only your own
devices can join. It works from anywhere (home, school, work, mobile data), and NEXUS is never
exposed to the public internet.

**On the PC (once):**
1. Install Tailscale from https://tailscale.com/download/windows and sign in.
2. In NEXUS, open **Settings → Your phone** and press **Turn on phone access**. It finds the PC's
   private address (for example `https://gaming-pc.tail1234.ts.net`), lets NEXUS answer to it,
   turns on the private HTTPS link, and shows a QR code to scan. No restart needed.
   Tick **Start NEXUS when I sign in** there too, so the phone can always reach it.
   (The script does the same from PowerShell, in the nexus folder, and can also make NEXUS start
   with Windows: `powershell -ExecutionPolicy Bypass -File scripts\phone_setup_windows.ps1`.)

**If NEXUS runs on a Mac instead:** install Tailscale from the Mac App Store and sign in, then
use **Settings → Your phone → Turn on phone access** the same way (or, in Terminal,
`cd ~/nexus && ./scripts/phone_setup_mac.sh`, which can also start NEXUS when you log in). The phone reaches a
Mac only while it's awake, so leave it plugged in with sleep prevented if you want to capture
from away; when it's asleep, the phone app still works from its saved copy.

**On the iPhone:**
1. Install **Tailscale** from the App Store and sign in with the **same account**.
2. Open **Safari** and go to the address the script printed.
3. Tap **Share**, then **Add to Home Screen**. NEXUS now opens like an app.

**When the PC is off or asleep**, the phone app still opens, and you can still capture text,
photos and voice notes. They're kept on the phone (you'll see "*N* to sync" at the top) and sent
automatically the next time NEXUS is reachable. Each capture carries its own id, so a re-send
never creates a duplicate, and it keeps the time you actually captured it.

**You can also still browse your archive when the PC is off.** While NEXUS is reachable, the
phone quietly keeps a read-only copy: every entry, project, relationship and art analysis, plus
small thumbnails of up to 500 pictures. It refreshes about every 5 minutes while the app is open.
- When the PC is off, Capture, Archive, Ideas, Projects, entries, Art and Search keep working
  from that copy.
- A banner says it's the phone's copy and when it was saved.
- Search matches words only; search by meaning needs NEXUS.
- Editing, re-running the AI and voice-note playback need NEXUS.
- When the PC is back, the banner goes away on its own and waiting captures are sent.
- The copy lives only on your phone. On the PC itself, NEXUS always reads straight from its database.

**Passcode (optional, recommended).** On the PC, go to **Settings → Passcode for other devices**
and choose a passcode.
- Your phone (or any other device) asks for it once and then stays unlocked.
- The PC itself never asks.
- Wrong guesses get slower and slower.
- **Sign out all devices** makes every device enter it again.
- Only the PC can set, change or remove the passcode.
- Captures made on a locked phone wait on the phone until it's unlocked.

If the script says HTTPS certificates are off, open https://login.tailscale.com/admin/dns, turn
on **MagicDNS** and **HTTPS Certificates**, and run it again.

## Updating NEXUS

**Easiest (from 0.14):** on the computer NEXUS runs on, open **Settings → Updates → Check for
updates**. It lists what's new; **Update now** does everything below in the background, and the
page reloads into the new version by itself. (It's offered only when the NEXUS folder has no
changes of its own, and never on phones.) The terminal commands below do the same thing.

One command does everything: it stops NEXUS (including the hidden one the desktop icon starts),
downloads the new version, installs anything new, and starts NEXUS again. Your ideas in `data`
and your `.env` are never touched. The database upgrades itself.

**Windows (PowerShell):**
```powershell
cd $HOME\nexus
powershell -ExecutionPolicy Bypass -File scripts\update_windows.ps1
```

**Mac (Terminal):**
```bash
cd ~/nexus && ./scripts/update_mac.sh
```

**Then, on your phone:**
1. Open NEXUS while the computer is on. It notices the new version and reloads by itself (or
   close it fully and open it again).
2. Wait a few seconds. **Settings → This device** should say **Copy saved on this device**.
   From then on it works with the computer off.

Stopping NEXUS before updating matters: if the old one keeps running, your phone keeps getting
the old version. (The first time only, use the manual steps if `update_windows.ps1` doesn't exist
yet: close NEXUS, `git pull`, then run the update script.)

### Manual setup (any OS)

```bash
python3 -m venv .venv && source .venv/bin/activate   # Windows: .venv\Scripts\activate
pip install -r requirements.txt -r requirements-local.txt
cp .env.example .env
ollama pull gemma3:12b
python run.py
```

- The database (`data/nexus.db`) and upload folders are created on first start.
- Development auto-reload: `python run.py --reload`. API docs: http://localhost:8000/docs

### Optional: use the Windows PC as the AI machine

A desktop with an NVIDIA RTX 3060 generates text faster than a laptop. To let the Mac use the PC's Ollama:

1. Install Ollama on the PC.
2. Set the Windows environment variable `OLLAMA_HOST=0.0.0.0`, then restart Ollama.
3. On the PC, run `ollama pull gemma3:12b`. This needs a 3060 with 12 GB of video memory; the 8 GB version should use `gemma3:4b`.
4. In the Mac's `.env`, set `NEXUS_OLLAMA_URL=http://<the PC's local IP>:11434`.

Ollama has **no password**, so only do this on your own home network, never on public Wi-Fi.

### Build the downloadable app (for other people)

`scripts\build_app_windows.ps1` (on Windows) or `./scripts/build_app_mac.sh` (on a Mac) builds
NEXUS as a standalone app in `dist/`, zipped, that runs without Python or Git: unzip and
double-click. Its data lives in the per-user app folder (`%LOCALAPPDATA%\NEXUS` or
`~/Library/Application Support/NEXUS`), not next to the app. Ollama is still installed
separately. On Windows, with Inno Setup installed (`winget install JRSoftware.InnoSetup`), it
also makes a normal installer, `NEXUS-Setup-<version>.exe`. Details and what's still missing for
a public release: `RELEASE.md`.

### Run the tests

```bash
python -m pytest
```

Tests use a temporary data folder and a fake Ollama. They never touch your real archive or download models.

### Voice recording note

Browsers only allow microphone access on `localhost` or HTTPS. Open NEXUS at
`http://localhost:8000` (not your machine's LAN IP) to use **Record Idea**.

---

## Using NEXUS

- **Home** (click **NEXUS**): a large "What are you thinking about?" box. Type, then
  **CAPTURE** (or Ctrl/⌘+Enter). **Record Idea** records audio in the browser;
  **Upload image / file** (or drag and drop) stores sketches, images, audio and documents.
  Unsent text is kept as a draft in your browser, so a refresh doesn't lose it.
  Choose or drop **many files at once** and each becomes its own entry. Tick **Date each one by
  the file's own date** when importing older work, so it lands in the right month on the Timeline.
- **First run**: the home screen welcomes you and checks whether this computer is ready (AI
  model, voice notes, search by meaning), each with a green or red light and what to do.
- **Keyboard shortcuts** (desktop): `/` search, `n` new capture, `a` ask, `m` map, `t` today,
  `←` / `→` previous / next entry,
  `?` shows the list. Ctrl/⌘+Enter captures.
- **Today**: what you captured today, recurring patterns (each shown with the entries
  behind it), and recently touched entries.
  - **Rediscover** brings back ideas captured on this day of an earlier month or year, and a few
    unfinished ideas you haven't touched for over a month (a different few each day).
- **Creative Archive**: every entry as a card. Filter by type, status, project, kind (pictures,
  voice notes, written, files), tag and date.
  Sort by newest, oldest, recently modified or alphabetical.
  - **Select** lets you pick several entries (or **Select all shown**) and act on them together:
    add them to a project, set their status, add a tag, export them as a `.nexus` file, or move them
    to the trash. Each entry's history records the change.
  - **Tags** lists every tag with its count. **Rename** fixes a typo everywhere; renaming onto an
    existing tag merges the two. **Delete** removes a tag from every entry (AI tags removed this way
    aren't added back).
  - **Timeline** shows everything month by month as picture tiles, like flipping back through a
    sketchbook. Jump to any month; **Show older** loads the rest.
  - **Characters** gathers each character (an entry of type `character`, or tagged `character`)
    with everything linked to it (sketches, scenes, "character in" links) and every entry whose own
    words mention its name. Links show whether you made them or the AI suggested them; name
    mentions are a plain text match, not AI. **Places & worlds** does the same for `worldbuilding`
    entries (or ones tagged `place`, `world`, `location` or `setting`). Pick a project to see just
    its cast (a project's page has a **Characters** button). Both work on the phone offline.
- **Projects**: create projects and capture straight into them. An entry can belong to many projects.
  - **Analyze this project**: the local AI reads every entry in the project (your words, notes,
    transcripts, and the art analyses of your sketches). It describes the **characters** and where
    they appear, the **story threads**, the **world**, the **strongest ideas**, **contradictions**
    between entries, **open questions**, **gaps**, and a few optional next steps.
  - Every point links to the entries behind it.
  - It's a reading of your material, not canon: your entries stay the source of truth.
  - Each run is kept as a version, so you can compare how the project grew.
  - **Story bible (print)**: the whole project on one clean page, grouped into characters, places
    & worlds, story, art and notes, with your words and pictures (AI summaries only if you tick
    them, and labelled). Print it or save it as a PDF. Works on the phone offline too.
- **Ideas**: the **Idea Incubator**, meaning everything whose status is `raw` or `exploring`. Nothing here is turned into a task.
- **Search**: natural queries such as `memory`, `villains`, `ideas about alternate realities`,
  `unfinished comic ideas`, `sketches with monsters`. NEXUS shows how it understood your query.
  It matches your **words** and, with the local embedding model, the **meaning**. For example,
  "forgetting who you are" finds an idea about memory loss. Each result shows how it matched.
  If nothing matches because of a typo, it asks **Did you mean …?** using words from your archive.
- **Map** (desktop menu; **Connections map** on the Archive page; **See connections** on a project):
  - Your ideas appear as dots, colored by type (pictures show as their thumbnail), with lines for
    their links. Linked ideas pull together, so clusters show which ideas belong together.
  - Filter by project, show AI suggestions as dashed lines, or include ideas without links.
  - Drag to move, pinch/scroll or **+ / −** to zoom, and click an idea to open it. Hovering an idea
    highlights its own links.
  - Works from the phone's offline copy too.
- **Ask** (desktop menu, or **Ask a question instead** on the Search page): ask about your own
  ideas, such as "What villains have I come up with?" or "Which ideas involve memory?".
  - NEXUS finds the entries most likely to hold the answer.
  - The local model answers **only from those entries**, keeping what you wrote apart from what
    an AI described.
  - Every point links to the entry it came from, and the entries used are shown under the answer.
  - A citation of an entry that wasn't provided is marked `[unknown entry]` instead of being linked.
  - Nothing is saved; asking never changes your archive.
- **Trash**: entries and projects you trash are hidden, not destroyed. Trashing a project never
  touches its entries. Open the Trash from the Creative Archive or Settings.
  - **Restore** brings anything back exactly as it was.
  - **Delete forever** (or **Empty trash**) permanently removes an item, with its files and history.
    Only things already in the Trash can be deleted, and older backups may still contain them.
- **Duplicates**: uploading a file you've already saved keeps both. NEXUS tells you, and the entry
  shows "Same file as …".
  **Creative Archive → Duplicates** (shown when there are any) groups identical files, marks the
  copies that have your own words, and moves extra copies to the Trash (restorable) one by one or
  with **Keep the oldest, trash the others**.
- **Entry detail**: your material, notes, transcript, AI summary, interpretation, inference and
  uncertainty, each visually distinct. Opened from a grid (Archive, Timeline, a project, Art…), it
  has **← Previous / Next →** through that list: the buttons, the arrow keys, or a swipe on the
  picture on a phone. **Tap a picture** to see it full screen; tap again for full size. You can:
  - edit the title, type and status (your choices are never overwritten by the AI)
  - confirm the AI's title and type
  - add notes
  - add and remove tags (a removed AI tag doesn't come back)
  - add the entry to projects, or **start a project from it** (named after it, same kind)
  - **accept / reject / ignore** AI-suggested relationships
  - **link it to another idea yourself** (*Related ideas → Link to another idea*: find it, pick how
    they're related, e.g. "character in"), or remove a link. A link you remove isn't suggested again.
  - re-run the organizer
  - browse the full revision history: each version shows what changed, and **Restore this version**
    brings an old one back as a new version, so nothing is lost
  - move the entry to the trash
  - on a **multi-page PDF** (a comic issue, a scanned sketchbook), **Split into pages**: each page
    becomes its own picture entry, linked to the PDF ("part of") and in the same projects, with
    previous/next page links. The PDF itself is never changed. Up to 80 pages.
    Afterwards, **Pages 1–6** (and so on) reads the pages in order as a comic sequence with the local AI.
- **Settings → Something not working?** makes a problem report (versions, whether the AI and
  search are healthy, counts, recent log lines, never your entries' text). You see it first and
  copy it yourself; nothing is sent anywhere.

---

## Visual Art Intelligence

NEXUS reads artwork the way an illustrator, comic artist, storyboard artist and visual storyteller would.
It covers comics, manga, cartooning, character, creature and environment design, storyboards, concept art
and illustration. **It is not the artist**: it observes, understands, explains, connects, questions,
suggests and compares, and you make every decision.

**What it does:**
- **Every new image gets a layered analysis automatically.**
  - A literal description comes first, with no invented details, and anything unclear is marked unclear.
  - Then: composition and eye flow, shape language, linework, value, lighting, color, perspective and depth,
    character, environment, and storytelling (*visible* information kept apart from *possible interpretation*).
  - Sequential-art notes: panel function, shot type and camera.
  - Suggestions in three levels: **Preserve** (what already works), **Develop** (what could be stronger, and
    why) and **Experiment** (optional, never required).
  - Several **creative directions** (Option A / B / C) without picking one for you, plus questions for you.
  - Stylization isn't treated as a mistake, and cartoons are judged as cartoons.
- **Modes** (on any image's page, or from **Art → Artwork** with images selected in order):
  - **Critique**: what I see, what's working, what could be stronger, why it matters, approaches, questions,
    optional experiments.
  - **Artist mode**: a collaborator that asks questions (ask it something specific if you like).
  - **Comic page**: layout, panel hierarchy, reading order (it doesn't assume one if it's ambiguous) and
    page-turn potential.
  - **Storyboard**: camera, staging, pacing and clarity. Rendering isn't judged.
  - **Sequence**: several panels. For each: what does the reader learn, and what changes?
  - **Compare A / B**: what changed and its effect. No "winner".
  - **Character consistency**: differences, which ones look intentional, and what stays constant.
  - **Evolution**: how versions of a piece developed.
- **Every analysis is kept.** Running again adds a new version (`artwork_analyses`).
- **Art development history:** on a newer drawing, choose "This is a new version of…". Then use
  **Show how it evolved** to see the timeline and analyze the evolution.
- **Creative memory (Art → Creative memory):**
  - Preferences you confirm are passed to the AI as context, never as rules.
  - When at least 3 different artworks share a trait (for example "heavy silhouettes"), NEXUS *suggests* it as
    an observed pattern and shows the artworks behind it. It's only used once you confirm it, and rejected
    patterns never come back.
- **Art library (Art → Art library):** add notes from your art books and courses (anatomy, perspective,
  storyboarding, inking…). Relevant notes are given to the AI, and the analysis names the ones it drew on.

Visual Art Intelligence needs a local model that can see images (`gemma3:12b` can).

**Picture formats.** You can upload JPEG, PNG, TIFF, PSD/PSB, TGA, WebP, BMP, GIF, HEIC/HEIF (iPhone),
AVIF, SVG, Illustrator (`.ai`), PDF (first page), Procreate, Krita (`.kra`), OpenRaster (`.ora`), ICO
and WMF/EMF. WMF/EMF previews only work on Windows.
- The original file is stored untouched and can always be downloaded.
- For formats the browser or the model can't read, NEXUS makes a viewable JPEG/PNG **preview** in
  `data/previews/`. The preview is derived data and can be rebuilt from the original at any time.
- Very large JPEG/PNG files get a smaller preview so they can still be analysed.
- PSD, Procreate and Krita files are shown and analysed as the flattened picture, not layer by layer.
Set `NEXUS_ART_AUTO_ANALYZE=false` to analyze only when you press a button.

## Sharing with a friend's NEXUS

Two people can each keep their own private NEXUS and still send each other ideas and art.
- **Send:** open an entry and press **Export this entry**, or open a project and press
  **Export project (.nexus)**. You get one `.nexus` file. Send it any way you like (AirDrop,
  email, a USB stick). Nothing goes through a server. The first time, NEXUS asks for your name,
  which is shown to whoever imports it.
- **Receive:** **Settings → Import from someone's NEXUS → Choose a .nexus file**.
- **What travels:** their words, notes, transcripts, their own tags and title/type choices, and
  every original file, byte for byte. Each file's fingerprint is checked on arrival.
- **What stays behind:** AI summaries, AI tags and art analyses. Your NEXUS organizes the
  imported entries again with its own local AI.
- **Attribution:** imported entries are labeled **from Sebas**, their words show as
  **Sebas's material** and their notes as **Sebas's notes**. **Your notes** stay yours.
- **Projects:** a shared project joins your project of the same name, or a new one is created.
  Links they made between the shared entries come along too.
- **No duplicates:** importing the same file twice adds nothing. Exporting and re-importing on
  the same NEXUS is refused.

## Backups

Your ideas and art are irreplaceable, so NEXUS backs them up.
- **What a backup contains:** one zip with the database and every original file you've
  uploaded (pictures, voice notes, documents). Previews, thumbnails and the search index are
  left out, because NEXUS rebuilds them.
- **Automatic:** a new backup is made every 7 days (`NEXUS_BACKUP_EVERY_DAYS`), and the newest 4
  are kept (`NEXUS_BACKUP_KEEP`).
- **Manual:** **Settings → Backups → Back up now**. You can also download any backup from there.
- **Where:** by default in `data/backups`. Point `NEXUS_BACKUP_DIR` at **another drive**, a USB
  stick or a OneDrive/iCloud folder, so one broken disk can't take your archive and its
  backups together. For example, in `.env` on Windows: `NEXUS_BACKUP_DIR=D:\NEXUS Backups`.

**Readable export.** **Settings → Backups → Export as readable files** gives you everything as
plain files:
- a Markdown file for every entry and project, with front matter (type, status, tags, projects) and
  your words, notes, transcript and the AI's layers in separate, labeled sections
- links between related entries
- every original file

It opens in any text editor or notes app (Obsidian, VS Code …), so your work never depends on
NEXUS. (`GET /api/export/markdown`)

**Restoring a backup.** Close NEXUS first, then run:

```powershell
cd $HOME\nexus
.venv\Scripts\python.exe -m app.services.backup restore "D:\NEXUS Backups\nexus-backup-20260927-120000.zip"
```

On a Mac: `cd ~/nexus && .venv/bin/python -m app.services.backup restore ~/path/to/backup.zip`.
Your current data is never deleted. It's moved to a `data-before-restore-…` folder next to
`data`, so a restore can be undone. You can also make a backup from the command line:
`python -m app.services.backup create`.

## Configuration (`.env`)

**Most people never need to edit this file.** Everyday choices are in the app under
**Settings → Preferences** and apply straight away:
- your name (for sharing)
- the AI model and the model for pictures
- the search-by-meaning model and how strict it is
- the speech-to-text model and voice-note language
- automatic picture analysis
- the backup folder, how often to back up and how many backups to keep

They're saved in `data/preferences.json` and override the same settings in `.env`.
**Reset to defaults** goes back to `.env`.

| Variable | Default | Purpose |
|---|---|---|
| `NEXUS_HOST` | `127.0.0.1` | Bind address. Keep it on loopback. |
| `NEXUS_PORT` | `8000` | Port |
| `NEXUS_ALLOWED_HOSTS` | `localhost,127.0.0.1` | Accepted `Host` headers (DNS-rebinding protection) |
| `NEXUS_DATA_DIR` | `./data` | Uploads + default SQLite location |
| `NEXUS_DATABASE_URL` | `sqlite:///<data>/nexus.db` | Any SQLAlchemy URL |
| `NEXUS_MAX_UPLOAD_MB` | `100` | Upload size limit |
| `NEXUS_AI_PROVIDER` | `ollama` | `ollama` (local model), `local` (offline heuristics only). Reserved: `openai`, `anthropic` |
| `NEXUS_OLLAMA_URL` | `http://127.0.0.1:11434` | Where Ollama runs |
| `NEXUS_OLLAMA_MODEL` | `gemma3:12b` | Model for organizing. It also reads images if it supports vision |
| `NEXUS_OLLAMA_VISION_MODEL` | empty | Separate model for images (empty means use the main model) |
| `NEXUS_OLLAMA_TIMEOUT` | `300` | Seconds to wait for one answer |
| `NEXUS_OLLAMA_KEEP_ALIVE` | `10m` | How long Ollama keeps the model loaded after use |
| `NEXUS_RELATIONSHIP_THRESHOLD` | `0.25` | Minimum confidence for storing a suggested relationship |
| `NEXUS_TRANSCRIPTION_PROVIDER` | `whisper_local` | `whisper_local`, `placeholder`. Reserved: `openai_whisper` |
| `NEXUS_WHISPER_MODEL` | `small` | `tiny`, `base`, `small`, `medium`, `large-v3`, `turbo` (bigger is more accurate but slower) |
| `NEXUS_WHISPER_LANGUAGE` | empty | Empty means auto-detect. Or set a code such as `en` |
| `NEXUS_WHISPER_DEVICE` / `_COMPUTE_TYPE` | `auto` / `auto` | CPU on Mac; CUDA is used automatically on NVIDIA |
| `NEXUS_EMBEDDING_PROVIDER` | `ollama` | `ollama` (search by meaning) or `none` (words only) |
| `NEXUS_EMBEDDING_MODEL` | `nomic-embed-text` | Local embedding model (about 0.3 GB) |
| `NEXUS_SEMANTIC_MIN_SIMILARITY` | `0.6` | How close (0–1) a meaning match must be. Raise it if unrelated ideas show up; lower it if related ones are missed |
| `NEXUS_SEMANTIC_MARGIN` | `0.08` | A meaning match must also beat the archive's typical score for that search by this much, so a search with nothing relevant returns nothing |
| `NEXUS_SEMANTIC_WINDOW` | `0.12` | Keep only meaning matches within this distance of the best one |
| `NEXUS_ART_AUTO_ANALYZE` | `true` | Analyze every new image with Visual Art Intelligence |
| `NEXUS_BACKUP_DIR` | `data/backups` | Where backups go. Use another drive or a cloud-synced folder |
| `NEXUS_BACKUP_EVERY_DAYS` | `7` | Automatic backup interval in days (`0` = off) |
| `NEXUS_BACKUP_KEEP` | `4` | How many backups to keep |
| `NEXUS_PROCESSING_MODE` | `background` | `background` (captures return instantly) or `sync` |
| `OPENAI_API_KEY`, `ANTHROPIC_API_KEY` | empty | Read from the environment only. Never stored in the database. |

---

## Security

- NEXUS binds to `127.0.0.1`. Do not expose it to a network or the internet.
  For phone access, use the Tailscale setup above: it keeps NEXUS on `127.0.0.1` and only your own
  signed-in devices can reach it.
- **Optional passcode** for every device except the computer NEXUS runs on:
  - The passcode is stored only as a salted PBKDF2 hash.
  - Unlocked devices get a signed, HttpOnly, SameSite=Strict cookie.
  - Signing everyone out replaces the signing key.
  - Guessing is rate-limited, and only the local computer can change the passcode.
  `run.py` warns you if the host isn't a loopback address.
- Requests with unexpected `Host` headers are rejected, which protects against DNS-rebinding attacks from web pages.
- The AI and transcription run on your machine. NEXUS only talks to the Ollama address you configure, and never through a proxy. No cloud AI service is contacted (none are implemented).
- Whisper downloads its speech model once from Hugging Face on first use. No audio is sent anywhere.
- Shared `.nexus` files are checked before import: paths are validated, each file's SHA-256 must
  match, sizes are limited, and file types are worked out from the files themselves, not from the
  labels inside the share.
- Uploaded files are saved under random names inside `data/` and served with `nosniff` and a
  sandboxing CSP. Original filenames are only kept as metadata.
- `.env` and everything in `data/` are git-ignored.

---

## Project layout

```
nexus/
├── app/
│   ├── main.py               FastAPI app, routers, static files, host protection
│   ├── config.py             Settings from environment / .env
│   ├── database.py           Engine, sessions, init_db()
│   ├── models.py             SQLAlchemy models + vocabularies (enums)
│   ├── schemas.py            Pydantic request/response models + serializers
│   ├── services/
│   │   ├── ai.py             AIProvider interface, LocalHeuristicProvider, reserved providers, registry
│   │   ├── ollama.py         OllamaProvider: local LLM, structured output, images, fallback
│   │   ├── processing.py     Background queue: transcribe → organize → index, crash recovery
│   │   ├── embeddings.py     Search by meaning: embedding provider, vector index, similarity
│   │   ├── art.py            Visual Art Intelligence: prompts, modes, answer schema, memory, library, evolution
│   │   ├── organizer.py      Organizer: runs the provider, stores derived data safely
│   │   ├── relationships.py  Relationship discovery + review
│   │   ├── search.py         SQLite FTS5 search, query parsing, SemanticSearchService seam
│   │   ├── transcription.py  TranscriptionService, local Whisper (faster-whisper), placeholder
│   │   ├── history.py        Revision snapshots and tag helpers
│   │   ├── insights.py       Evidence-backed pattern detection
│   │   ├── images.py         Picture formats: previews and thumbnails
│   │   └── text.py           Keyword/concept/theme lexicons used by the local provider
│   ├── routes/
│   │   ├── entries.py        Entries, revisions, relationships
│   │   ├── projects.py       Projects and membership
│   │   ├── search.py         POST /api/search
│   │   ├── uploads.py        Uploads; originals, previews and thumbnails
│   │   ├── system.py         Settings, tags, patterns
│   │   ├── offline.py        Snapshot for the phone's offline copy
│   │   └── art.py            Art analyses, gallery, evolution, creative memory, art library
│   └── templates/index.html  Single-page shell
├── static/css/style.css      Styles (dark/light, responsive)
├── static/js/app.js          Vanilla JS app (router, views, recorder)
├── data/{uploads,audio,images,previews,exports}/
├── scripts/                 setup_mac.sh, update_mac.sh, make_mac_app.sh, setup_windows.ps1, update_windows.ps1, make_windows_app.ps1, nexus_launcher.ps1, start_windows.bat, phone_setup_windows.ps1
├── static/sw.js, manifest.webmanifest, icons/   Installable phone app + offline support
├── tests/                    pytest suite (includes a fake Ollama)
├── requirements.txt          NEXUS itself
├── requirements-local.txt    Local Whisper
├── .env.example
└── run.py
```

---

## Database structure

| Table | Purpose |
|---|---|
| `creative_entries` | One captured item. **Raw:** `original_text`. **User:** `title`/`entry_type` when their `*_source` is `user`, `status`, `user_notes`, `user_confirmed`. **Derived:** `transcript`, `ai_summary`, `ai_interpretation`, `ai_inference`, `ai_metadata` (JSON: concepts, themes, keywords, uncertainties, potential projects, visual metadata, transcription status, rejected tags), `ai_provider` (which model actually did the work, including fallbacks), `analyzed_at`, `confidence`. `processing_status` (`queued`, `transcribing`, `analyzing`, `done`, `failed`). Also `source_type`, `source_file`, reserved `embedding_id/model/created_at`, timestamps and `deleted_at` (soft delete). |
| `attachments` | The original file for each upload: `kind`, `original_filename`, `stored_path`, `mime_type`, `size_bytes`, `sha256`, `created_at`. |
| `projects` | `name`, `description`, `project_type`, `status`, timestamps. |
| `project_entries` | Many-to-many link between projects and entries. |
| `relationships` | Graph edges: `source_entry_id`, `target_entry_id`, `relationship_type`, `confidence`, `origin` (`ai`/`user`), `status` (`suggested`/`accepted`/`rejected`/`ignored`), `rationale` (the evidence). |
| `tags` / `entry_tags` | Tags. `entry_tags.source` records whether you or the AI added the tag. |
| `creative_revisions` | Append-only JSON snapshots (`version_number`, `content_snapshot`, `change_description`). Written on capture, every edit, every AI analysis, project change, trash and restore. |
| `entry_search` | SQLite FTS5 index (porter stemming, BM25) over user and AI text. |
| `artwork_analyses` | **Derived, append-only.** One Visual Art Intelligence analysis: `entry_id`, `analysis_version`, `mode`, `related_entry_ids` (other images, in order), `question`, `status`, `model`, `visual_description`, and JSON for `artistic_analysis`, `composition_analysis`, `character_analysis`, `storytelling_analysis`, `sequential_art_analysis`, `strengths`, `development_opportunities`, `experiments`, `uncertainties`. `details` holds the mode's extras (critique, comparison, page, sequence, creative directions, questions, style signals, library references). |
| `art_preferences` | Creative memory: `statement`, `origin` (`user` / `observed`), `status` (`suggested` / `confirmed` / `rejected`), `evidence` (entry ids). |
| `art_knowledge`, `art_knowledge_chunks` | The art library (user material) and its passages, with optional meaning vectors for retrieval. |
| `entry_embeddings` | **Derived.** One meaning vector per entry (`model`, `dim`, normalised float32 `vector`, `content_hash`). Rebuilt automatically when the entry's text or the model changes. |

Enumerations (entry types, statuses, source types, project types, relationship types) are
stored as strings, which keeps them portable to PostgreSQL. New nullable columns are added to an
existing database automatically at startup (`database.add_missing_columns`), so upgrading NEXUS keeps your archive.

**The original is never lost:**
- `PUT` rejects `original_text`.
- `DELETE` is a soft delete, and `POST /api/entries/{id}/restore` undoes it.
- Uploaded files are never modified.
- AI failures leave the saved capture intact.

---

## How the AI provider works

All AI work goes through `app/services/ai.py`:

```python
class AIProvider(ABC):
    def analyze_entry(self, entry: EntryContext) -> AnalysisResult: ...
    def summarize_entry(self, entry: EntryContext) -> str: ...
    def find_relationships(self, entry, existing_entries) -> list[RelationshipSuggestion]: ...
    def answer_question(self, question, context) -> str: ...
```

- Providers receive plain `EntryContext` objects and return plain results. They never touch the database.
- `get_ai_provider()` selects a provider from the `PROVIDERS` registry using `NEXUS_AI_PROVIDER`.
- `Organizer` (`services/organizer.py`) is the only caller during capture and analysis. It:
  - writes results into the derived fields only
  - changes `title` / `entry_type` only if you haven't set them
  - respects tags you removed
  - records uncertainty
  - stores relationships as *suggested*
  - snapshots a revision
  - updates the search index
- **Default: `OllamaProvider`** (`services/ollama.py`) uses a language model on your machine:
  - **Analysis:** it sends your words (plus notes and transcript, clearly labelled) with a strict
    JSON schema. The reply is validated again: unknown types become `other`, confidences are
    clamped, lists are cleaned, and the model is told to separate interpretation, inference
    and uncertainty.
  - **Images:** if the model supports vision (Ollama reports this), the image (or its preview, for
    formats like TIFF, PSD or HEIC) is sent and the description is stored in `ai_metadata.visual`.
    It is shown as "what the model saw" and is searchable. A file that can't be converted is
    marked UNKNOWN.
  - **Long material:** a very long voice-note transcript or text (over about 12,000 characters,
    roughly a 15-minute recording) is read in parts. The model summarizes each part, then analyzes
    the whole from those summaries, so the end isn't cut off. The analysis says it did this. The
    full text is kept unchanged.
  - **Relationships:** a cheap keyword pre-filter picks up to 8 candidates, then the model judges
    which are really related and why. Candidate ids the model invents are discarded.
  - **Fallback:** if Ollama is down, the model is missing, or the reply is unusable, the offline
    `LocalHeuristicProvider` does the work. The entry records why, and `ai_provider` says
    `(fallback)`.
- `LocalHeuristicProvider` works fully offline with keyword, concept and theme lexicons (`services/text.py`).
- `OpenAIProvider` and `AnthropicProvider` are registered stubs.
- **Background processing** (`services/processing.py`):
  - A capture is committed first, then queued.
  - A single worker thread transcribes (voice), then organizes, then indexes for search by meaning, one entry at a time.
  - The UI shows "organizing…" and updates by itself.
  - Unfinished work resumes after a restart. Entries missing from the meaning index are indexed at startup.
  - **Locking:** slow model calls run with no database write pending, and results are written
    in one quick step on a fresh read. So capturing never blocks while the model thinks, and edits
    made meanwhile are kept.
- **Search by meaning** (`services/embeddings.py` and `SemanticSearchService` in `services/search.py`):
  - Each entry gets an embedding vector from a local Ollama model (default `nomic-embed-text`).
    The vector is built from your words, notes, transcript, AI summary, concepts, the image
    description and tags.
  - Searching embeds the query and scores entries by cosine similarity. That list is merged
    with the word matches using reciprocal-rank fusion.
  - Type and status filters stay strict. If nothing matches them, NEXUS shows looser matches and says so.
  - "Related ideas" also use the index: the closest entries by meaning are among the candidates the model judges.
  - If the model isn't installed or Ollama is down, search quietly falls back to words and shows a note.

**Adding a real provider:**
1. Subclass `AIProvider`.
2. Implement the four methods (read API keys from `os.environ`).
3. Set `external = True` if it sends data off the machine.
4. Register it in `PROVIDERS`.
5. Set `NEXUS_AI_PROVIDER`.

Transcription follows the same pattern in `services/transcription.py`: `TranscriptionService.transcribe(audio_path)` delegates to a `TranscriptionProvider`.
- `LocalWhisperProvider` runs faster-whisper with voice-activity detection and auto language detection.
- The transcript is stored separately from the audio and labelled "machine-generated". You can correct it; the recording never changes.
- The placeholder provider never invents text.

---

## API

| Method | Path | |
|---|---|---|
| POST | `/api/entries` | Capture text (`original_text`, optional `title`, `entry_type`, `status`, `tags`, `project_ids`, `analyze`, `client_id`, `captured_at`). A repeated `client_id` returns the existing entry (200) instead of a duplicate |
| GET | `/api/entries` | List (`entry_type`, `status`, `source_type`, `project_id`, `tag`, `date_from`, `date_to`, `sort=newest\|oldest\|modified\|alpha`, `limit`, `offset`) |
| GET | `/api/entries/{id}` | Detail |
| PUT | `/api/entries/{id}` | Edit title/type/status/notes/transcript/tags/confirmation (not `original_text`) |
| DELETE | `/api/entries/{id}` | Soft delete |
| POST | `/api/entries/{id}/restore` | Restore from trash |
| POST | `/api/entries/{id}/analyze` | Re-run the AI organizer (returns 202 while queued) |
| POST | `/api/entries/{id}/transcribe` | (Re)transcribe a voice entry, then re-organize (`force=true` to replace a hand-corrected transcript) |
| GET | `/api/entries/{id}/revisions` | Revision history |
| GET | `/api/entries/{id}/relationships` | Relationships (`include_rejected=true` to see rejected ones) |
| POST | `/api/entries/{id}/relationships` | Create a relationship yourself |
| PATCH | `/api/relationships/{id}` | `{"status": "accepted"\|"rejected"\|"ignored"}` |
| POST | `/api/uploads` | Multipart: `file`, optional `note`, `title`, `entry_type`, `project_id`, `analyze`, `client_id`, `captured_at` |
| GET | `/api/uploads/{attachment_id}` | The original file |
| POST | `/api/search` | `{"query": "...", "limit", "entry_types", "statuses", "date_from", "date_to"}`. Each result has `matched_by` (`words`, `meaning`, `filters`) and `similarity` |
| POST | `/api/search/reindex` | Queue entries whose meaning index is missing or stale |
| GET · POST · PUT/DELETE | `/api/lock` · `/api/lock/unlock` · `/api/lock/passcode` | Passcode status · unlock this device · set/remove the passcode (this computer only) |
| GET/PUT | `/api/preferences` | Everyday settings changed in the app (`null` resets one) |
| GET | `/api/graph` | Connections map: `nodes` and `edges` (`project_id`, `include_suggested`) |
| POST | `/api/ask` | `{"question": "..."}` → an answer from the archive with `sources` (each `cited` or not). Read-only |
| GET | `/api/uploads/{attachment_id}/preview` · `/thumb` | A viewable JPEG/PNG of any picture format · a small version for cards |
| GET | `/api/offline/snapshot` | Everything the phone needs to browse the archive offline (gzip) |
| GET/POST | `/api/backups` · GET `/api/backups/{name}` | List / start a backup · download one |
| GET | `/api/share/export` | A `.nexus` file: `entry_ids=1,2` or `project_id=n`, and `by=<your name>` |
| POST | `/api/share/import` | Multipart `file`: import a `.nexus` file. Returns `imported`, `already_had`, `project_id` |
| GET | `/api/trash` | Entries in the trash |
| POST | `/api/entries/{id}/revisions/{n}/restore` | Bring back version *n* (recorded as a new version) |
| POST | `/api/entries/bulk` | `{entry_ids, action: add_to_project\|remove_from_project\|set_status\|add_tag\|trash, project_id?, status?, tag?}` |
| DELETE · POST | `/api/trash/entries/{id}` · `/api/trash/projects/{id}` · `/api/trash/empty` | Delete forever (trashed items only) |
| PATCH · DELETE | `/api/tags/{name}` | Rename (merges into an existing tag) · remove from every entry |
| GET/POST | `/api/projects` | List / create |
| POST/GET | `/api/projects/{id}/analyses` | Start a project analysis (202 while running) / every version, newest first |
| GET/PUT | `/api/projects/{id}` | Detail with entries / update |
| POST | `/api/projects/{id}/entries` | `{"entry_id": n}` |
| DELETE | `/api/projects/{id}/entries/{entry_id}` | Remove from project |
| DELETE | `/api/projects/{id}` | Move a project to the trash (its entries are untouched) |
| POST | `/api/projects/{id}/restore` | Restore a trashed project (`GET /api/projects?trashed=true` lists them) |
| POST | `/api/entries/{id}/art` | Start an art analysis: `{"mode", "related_entry_ids", "question"}` (returns 202 while running) |
| GET | `/api/entries/{id}/art` | All art analysis versions of an artwork |
| GET | `/api/art/analyses/{id}` | One analysis |
| GET | `/api/art/gallery` | Every artwork, with analysis counts |
| GET | `/api/entries/{id}/evolution` | Versions linked by `evolved_from`, oldest first |
| GET/POST | `/api/art/preferences` · PATCH `/api/art/preferences/{id}` | Creative memory; confirm or reject observed patterns |
| GET/POST | `/api/art/knowledge` · PUT/DELETE `/api/art/knowledge/{id}` | Art library |
| GET | `/api/tags`, `/api/settings`, `/api/insights/patterns` | Tags with counts, configuration (no secrets), evidence-backed patterns |

---

## Not built yet

- Deeper project analysis: page-by-page pacing and visual consistency across a whole comic, by
  looking at the pages themselves (today's project analysis reads the entries and their art analyses).
- Importing PDFs or ebooks into the art library (for now, paste the passages you care about).

- Knowledge-graph entities beyond entries: people, places, themes and events.
- Tracking who created each entry when several people share *one* NEXUS (separate installs
  already mark shared entries with their author).
- Alembic migrations for non-additive schema changes, and a PostgreSQL option.
- Cloud providers (OpenAI, Anthropic), for use only if you ever opt in.
