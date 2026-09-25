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

This is milestone 1: it runs entirely on your computer, and by default it never sends anything anywhere.

---

## Setup

Requires **Python 3.10+**.

```bash
git clone <this repo> nexus
cd nexus

python3 -m venv .venv
source .venv/bin/activate          # Windows: .venv\Scripts\activate

pip install -r requirements.txt
cp .env.example .env               # optional: the defaults work as they are

python run.py
```

Open **http://localhost:8000** in your browser.

- The database (`data/nexus.db`) and upload folders are created on first start.
- To run on another port, set `NEXUS_PORT` in `.env`.
- Development auto-reload: `python run.py --reload`.
- Interactive API docs: http://localhost:8000/docs

### Run the tests

```bash
python -m pytest
```

Tests use a temporary data folder and never touch your real archive.

### Voice recording note

Browsers only allow microphone access on `localhost` or HTTPS. Open NEXUS at
`http://localhost:8000` (not your machine's LAN IP) to use **Record Idea**.

---

## Using NEXUS

- **Home** (click **NEXUS**): a large "What are you thinking about?" box. Type, then
  **CAPTURE** (or Ctrl/⌘+Enter). **Record Idea** records audio in the browser;
  **Upload image / file** (or drag and drop) stores sketches, images, audio and documents.
  Unsent text is kept as a draft in your browser, so a refresh doesn't lose it.
- **Today**: what you captured today, recurring patterns (each shown with the entries
  behind it), and recently touched entries.
- **Creative Archive**: every entry as a card. Filter by type, status, project, tag and date.
  Sort by newest, oldest, recently modified or alphabetical.
- **Projects**: create projects and capture straight into them. An entry can belong to many projects.
- **Ideas**: the **Idea Incubator**, meaning everything whose status is `raw` or `exploring`. Nothing here is turned into a task.
- **Search**: natural queries such as `memory`, `villains`, `ideas about alternate realities`,
  `unfinished comic ideas`, `sketches with monsters`. NEXUS shows how it understood your query.
- **Entry detail**: your material, notes, transcript, AI summary, interpretation, inference and
  uncertainty, each visually distinct. You can:
  - edit the title, type and status (your choices are never overwritten by the AI)
  - confirm the AI's title and type
  - add notes
  - add and remove tags (a removed AI tag doesn't come back)
  - add the entry to projects
  - **accept / reject / ignore** AI-suggested relationships
  - re-run the organizer
  - browse the full revision history
  - move the entry to the trash

---

## Configuration (`.env`)

| Variable | Default | Purpose |
|---|---|---|
| `NEXUS_HOST` | `127.0.0.1` | Bind address. Keep it on loopback. |
| `NEXUS_PORT` | `8000` | Port |
| `NEXUS_ALLOWED_HOSTS` | `localhost,127.0.0.1` | Accepted `Host` headers (DNS-rebinding protection) |
| `NEXUS_DATA_DIR` | `./data` | Uploads + default SQLite location |
| `NEXUS_DATABASE_URL` | `sqlite:///<data>/nexus.db` | Any SQLAlchemy URL |
| `NEXUS_MAX_UPLOAD_MB` | `100` | Upload size limit |
| `NEXUS_AI_PROVIDER` | `local` | `local` (offline heuristics). Reserved names: `openai`, `anthropic`, `local_model` |
| `NEXUS_RELATIONSHIP_THRESHOLD` | `0.25` | Minimum confidence for storing a suggested relationship |
| `NEXUS_TRANSCRIPTION_PROVIDER` | `placeholder` | Reserved names: `whisper_local`, `openai_whisper` |
| `OPENAI_API_KEY`, `ANTHROPIC_API_KEY` | empty | Read from the environment only. Never stored in the database. |

---

## Security

- NEXUS binds to `127.0.0.1` and **has no login**. Do not expose it to a network or the internet.
  `run.py` warns you if the host isn't a loopback address.
- Requests with unexpected `Host` headers are rejected, which protects against DNS-rebinding attacks from web pages.
- No external AI service is contacted unless you explicitly configure one (none are implemented yet).
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
│   │   ├── organizer.py      Organizer: runs the provider, stores derived data safely
│   │   ├── relationships.py  Relationship discovery + review
│   │   ├── search.py         SQLite FTS5 search, query parsing, SemanticSearchService seam
│   │   ├── transcription.py  TranscriptionService + providers (placeholder in v1)
│   │   ├── history.py        Revision snapshots and tag helpers
│   │   ├── insights.py       Evidence-backed pattern detection
│   │   └── text.py           Keyword/concept/theme lexicons used by the local provider
│   ├── routes/
│   │   ├── entries.py        Entries, revisions, relationships
│   │   ├── projects.py       Projects and membership
│   │   ├── search.py         POST /api/search
│   │   ├── uploads.py        File/voice/image uploads and original-file serving
│   │   └── system.py         Settings, tags, patterns
│   └── templates/index.html  Single-page shell
├── static/css/style.css      Styles (dark/light, responsive)
├── static/js/app.js          Vanilla JS app (router, views, recorder)
├── data/{uploads,audio,images,exports}/
├── tests/                    pytest suite
├── requirements.txt
├── .env.example
└── run.py
```

---

## Database structure

| Table | Purpose |
|---|---|
| `creative_entries` | One captured item. **Raw:** `original_text`. **User:** `title`/`entry_type` when their `*_source` is `user`, `status`, `user_notes`, `user_confirmed`. **Derived:** `transcript`, `ai_summary`, `ai_interpretation`, `ai_inference`, `ai_metadata` (JSON: concepts, themes, keywords, uncertainties, potential projects, visual metadata, transcription status, rejected tags), `ai_provider`, `analyzed_at`, `confidence`. Also `source_type`, `source_file`, reserved `embedding_id/model/created_at`, timestamps and `deleted_at` (soft delete). |
| `attachments` | The original file for each upload: `kind`, `original_filename`, `stored_path`, `mime_type`, `size_bytes`, `sha256`, `created_at`. |
| `projects` | `name`, `description`, `project_type`, `status`, timestamps. |
| `project_entries` | Many-to-many link between projects and entries. |
| `relationships` | Graph edges: `source_entry_id`, `target_entry_id`, `relationship_type`, `confidence`, `origin` (`ai`/`user`), `status` (`suggested`/`accepted`/`rejected`/`ignored`), `rationale` (the evidence). |
| `tags` / `entry_tags` | Tags. `entry_tags.source` records whether you or the AI added the tag. |
| `creative_revisions` | Append-only JSON snapshots (`version_number`, `content_snapshot`, `change_description`). Written on capture, every edit, every AI analysis, project change, trash and restore. |
| `entry_search` | SQLite FTS5 index (porter stemming, BM25) over user and AI text. |

Enumerations (entry types, statuses, source types, project types, relationship types) are
stored as strings, which keeps them portable to PostgreSQL.

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
- **Default: `LocalHeuristicProvider`**. It works fully offline with keyword, concept and theme lexicons (`services/text.py`). It is a first pass rather than a language model: titles and summaries are built from your own first sentence, and its uncertainty is recorded.
- `OpenAIProvider`, `AnthropicProvider` and `LocalModelProvider` are registered stubs. Selecting one records a clear "not implemented" error on each entry, and your capture is still saved.

**Adding a real provider:**
1. Subclass `AIProvider`.
2. Implement the four methods (read API keys from `os.environ`).
3. Set `external = True` if it sends data off the machine.
4. Register it in `PROVIDERS`.
5. Set `NEXUS_AI_PROVIDER`.

Transcription follows the same pattern in `services/transcription.py`: `TranscriptionService.transcribe(audio_path)` delegates to a `TranscriptionProvider`. The v1 placeholder deliberately leaves the transcript empty (UNKNOWN) rather than inventing text.

---

## API

| Method | Path | |
|---|---|---|
| POST | `/api/entries` | Capture text (`original_text`, optional `title`, `entry_type`, `status`, `tags`, `project_ids`, `analyze`) |
| GET | `/api/entries` | List (`entry_type`, `status`, `source_type`, `project_id`, `tag`, `date_from`, `date_to`, `sort=newest\|oldest\|modified\|alpha`, `limit`, `offset`) |
| GET | `/api/entries/{id}` | Detail |
| PUT | `/api/entries/{id}` | Edit title/type/status/notes/transcript/tags/confirmation (not `original_text`) |
| DELETE | `/api/entries/{id}` | Soft delete |
| POST | `/api/entries/{id}/restore` | Restore from trash |
| POST | `/api/entries/{id}/analyze` | Re-run the AI organizer |
| GET | `/api/entries/{id}/revisions` | Revision history |
| GET | `/api/entries/{id}/relationships` | Relationships (`include_rejected=true` to see rejected ones) |
| POST | `/api/entries/{id}/relationships` | Create a relationship yourself |
| PATCH | `/api/relationships/{id}` | `{"status": "accepted"\|"rejected"\|"ignored"}` |
| POST | `/api/uploads` | Multipart: `file`, optional `note`, `title`, `entry_type`, `project_id`, `analyze` |
| GET | `/api/uploads/{attachment_id}` | The original file |
| POST | `/api/search` | `{"query": "...", "limit", "entry_types", "statuses", "date_from", "date_to"}` |
| GET/POST | `/api/projects` | List / create |
| GET/PUT | `/api/projects/{id}` | Detail with entries / update |
| POST | `/api/projects/{id}/entries` | `{"entry_id": n}` |
| DELETE | `/api/projects/{id}/entries/{entry_id}` | Remove from project |
| GET | `/api/tags`, `/api/settings`, `/api/insights/patterns` | Tags with counts, configuration (no secrets), evidence-backed patterns |

---

## Not built yet

- Real AI providers (OpenAI, Anthropic, a local LLM via Ollama or llama.cpp). Only the interfaces exist.
- Real transcription (local Whisper or a cloud service).
- Image analysis to fill `ai_metadata.visual`.
- Embeddings and vector search behind `SemanticSearchService`, using the reserved `embedding_*` fields.
- Knowledge-graph entities beyond entries: people, places, themes and events.
- Questions over the archive (`answer_question`). The method exists but has no endpoint or UI.
- A trash view in the UI (the restore API already exists).
- Diff and restore between revisions.
- Exports into `data/exports`.
- Schema migrations with Alembic before the first schema change, and a PostgreSQL option.
- Relationship graph visualisation and manual linking in the UI (the API exists).
