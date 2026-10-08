# Teksting — project memory for Claude Code

Local Windows app that turns Norwegian corporate videos (10–60 min) into publication-quality SRT subtitles,
plus an editor to proofread them against the video. Built in a claude.ai session (Oct 2026), moved to
Claude Code on Jonas's PC on 2026-10-08. Read this fully before changing anything.

## The people and the goal
- **Jonas** (owner, builds this with Claude). Not a programmer: explain commands, prefer doing over telling,
  keep answers concise, challenge his ideas rather than agreeing, and use Socratic questions when he is learning.
- **End user**: a friend who subtitles corporate videos for a big company's website. Today he downloads videos
  from Vimeo, writes captions in Adobe Premiere Pro, and sends a **CSV of captions with timestamps** to a colleague.
- **Goal**: under 6 min of human work per 1 min of video (Jonas: "we should beat that by a lot").
  The app measures this ("Arbeidstid" per video, average vs goal on the Videoer page).
- **Hardware**: laptop CPU only. Transcribe overnight, proofread by day.
- **Promise shown in the app**: everything runs locally, nothing is uploaded. Don't add cloud services without
  asking Jonas (a cloud AI proofreader was discussed and rejected for this reason).

## Subtitle rules (Netflix-style, Norwegian)
42 chars/line, max 2 lines, 1–7 s per cue, max 17 chars/s, ≥0.08 s gap between cues, cue lingers 0.3 s after
last word, never join across a 1.2 s pause. Light editing: filler words (eh, ehm, øh, mm, hm…) removed.
Line breaks avoid ending a line on weak words (articles, prepositions, adjectives before nouns, numerals).
All tunables: `tekst/segment.py` class `P`; user-editable subset in Settings → "Regler for undertekster".

## Run it
- **Users**: double-click `start.bat` once (downloads uv → Python 3.12 → packages into `runtime\` and `.venv\`,
  creates Desktop + Start-menu shortcut "Teksting" via `lag-snarveier.ps1`), then use the shortcut
  (`Teksting.vbs` → hidden `pythonw server.py --app`).
- **Dev, real models**: `.venv\Scripts\python.exe server.py` → http://127.0.0.1:8765
- **Dev, fake mode (no models, instant)**: `dev\run_fake.bat` → http://127.0.0.1:8766 — fake transcripts,
  fake downloads, separate data folder `dev\fakedata`. Add any short .mp4 as a video. Use this for all UI work.
  Port 8766 so it can run alongside Jonas's real app on 8765.
- Env vars: `TEKSTING_FAKE=1`, `TEKSTING_DATA` (data folder, default `data\`), `TEKSTING_PORT` (8765),
  `TEKSTING_NO_BROWSER=1`, `TEKSTING_APP=1` or `--app` (Edge/Chrome app window + auto-exit),
  `TEKSTING_IDLE_EXIT` (seconds, default 90).
- Log when started from the shortcut: `data\teksting.log`.
- Build the zip for the friend: `powershell -ExecutionPolicy Bypass -File dev\pack.ps1` → `dist\Teksting.zip`
  (code only, CRLF for .bat/.vbs/.ps1/.txt; never include `data\`, `.venv\`, `runtime\`).
  Gmail blocks this zip (it contains .bat/.vbs) — share via OneDrive link.
- `dev\run_fake.bat` and `dev\pack.ps1` were recreated in Claude Code on 2026-10-08 (the originals never left
  the claude.ai session).

## Architecture
FastAPI + uvicorn backend, vanilla-JS single-page app (no build step), files on disk as the database.

| File | What it does |
|---|---|
| `server.py` | API, background `Worker` (transcription queue, one job at a time), `Exporter` (burn-in queue), `Tracker` (phase-weighted progress + ETA), app mode (Edge `--app` window, watchdog auto-exit when window closed and idle), Windows keep-awake while busy, tkinter file picker in a subprocess (DPI-aware) |
| `tekst/engine.py` | Model download (HF `NbAiLab/nb-whisper-{small,medium,large}`, ready-made `ct2/` folders + tokenizer/preprocessor files), faster-whisper transcription, **gap rescue**, `listen` (re-transcribe one cue), waveform peaks, fake mode |
| `tekst/segment.py` | Words → cues: filler removal, DP segmentation with break costs, line splitting, timing rules, SRT writer |
| `tekst/compare.py` | Disagreements between the main model and the fast model (double-check) |
| `tekst/learn.py` | Learns word corrections (original vs edited cues); auto-applies a rule after it's seen in ≥2 videos; user can set on/off/delete |
| `tekst/spell.py` | Hunspell nb_NO / nn_NO via spylls (LibreOffice dictionaries, downloaded on first use) |
| `tekst/burn.py` | Burned-in subtitles: Pillow renders one PNG per cue → ffmpeg overlay (imageio-ffmpeg binary), encoder fallbacks libx264 → h264_mf → libopenh264 → mpeg4. Output `<video>_tekstet.mp4` |
| `tekst/store.py` | Settings (`DEFAULT_SETTINGS`), glossary, projects under `data\projects\<id>\` |
| `static/app.js` | Router, Videoer page (queue, status, batch export, work-time stats), Settings page, toasts, tooltips (`data-tip`) |
| `static/editor.js` | Proofreading editor (class `Editor`) |
| `static/i18n.js` | All UI text, `nb` and `en`. **Every UI string goes here in both languages.** |
| `static/style.css` | Tokens at top (light + dark), later "polish v6" block overrides; dark "studio" editor left pane |

Project folder files: `meta.json`, `words.jsonl` (word timings), `cues.json` (current), `cues_orig.json`
(as produced, for learning), `disagree.json`, `rescued.json`, `autofix.json`, `peaks.bin` (uint8, 50/s).

### Transcription pipeline (Worker.process)
1. Ensure models downloaded (main + `small` if double-check is on).
2. `engine.transcribe(main, rescue=True)`: decode audio → faster-whisper (int8 CPU, beam 5, word timestamps,
   `vad_filter` with VAD = threshold 0.35 / min_silence 700 / pad 500, `condition_on_previous_text=False`,
   glossary + learned words passed as `hotwords`).
3. **Gap rescue**: text-less gaps ≥1.5 s that contain loud 1 s windows get re-transcribed without VAD; kept only
   if no hallucination pattern (`HALLU` regex) and mean word prob ≥0.45. Marked `"r": 1`, flagged in the editor.
   (Added because the model skipped a clearly spoken sentence — Jonas confirmed this fix worked.)
4. `make_cues` → learned rules applied (`autofix.json`) → saved.
5. Double-check: `small` model transcribes again; disagreements (ignoring spacing and non-words) → flags.
6. Waveform peaks. Status `done`.
Progress: engine reports `(p, phase, cap)` per phase (model/audio/vad/decode/rescue); `Tracker` maps phases
to time-weighted ranges, measures real speed during decode, sends `rate`/`cap`/`pt`; the UI extrapolates every
250 ms so the bar moves smoothly. `time_scale()` learns this PC's speed from finished jobs (`took/expected`).

### Editor features (all exist and were tested in fake mode)
Review mode ("Gå gjennom": jumps only to flagged cues, Enter approves), find/replace, style panel for burn-in
(font, size, colour, box, position — preview overlay matches the burned result), export menu (SRT next to the
video, download SRT, burn-in video), undo/redo (also for split/merge/delete; toast with "Angre"), flags
(spelling, reading speed, line length, model disagreement with "Bruk", rescued speech, autofix with "Angre"),
"Hør på nytt" (re-transcribe one cue), glossary popup when a word is corrected to a similar spelling,
work timer (counts only active time), pause-on-type, waveform timeline (drag to scroll, wheel scrolls,
Ctrl+wheel zooms, drag cue edges to retime).
Keys outside text fields: Space play/pause, ←/→ ±5 s, ↑/↓ previous/next cue, Enter edit, Esc leave field.
In text fields: Ctrl+Space, Alt+arrows. Alt+G review, Alt+R replay, Alt+I/O start/end here, Alt+K approve,
Ctrl+F find, Ctrl+Z/Y undo/redo. Hover tooltips only on non-obvious buttons (Jonas's explicit wish).

## Decisions already made (don't relitigate without reason)
- NB-Whisper large is the default model (best Norwegian accuracy); medium/small optional.
- No audio preprocessing (denoise etc.) — Jonas declined it.
- `av<17` pinned (PyAV 17+ broke faster-whisper's `metadata_errors` arg).
- Windows only; zip + start.bat distribution; models download automatically.
- Python is fetched by uv into the app folder (`UV_PYTHON_PREFERENCE=only-managed`), never the system Python.
- UI is Norwegian-first, English toggle.
- Git: local repo + private GitHub remote `origin` =
  https://github.com/7wpjh2cf4h-netizen/Tekste-prosjekt-sebastian (branch `main`). Commit after each verified
  change and push when a piece of work is done.

## Status: tested vs NOT tested
Tested (Linux, fake mode + Playwright headless): every UI flow, burn-in with real ffmpeg, learning, progress bar,
timeline drag, app-mode watchdog. Tested with real models (by Jonas on Windows, earlier versions): transcription
quality, gap rescue, start.bat install.
**Not yet verified on Windows**: shortcuts + `Teksting.vbs` hidden launch, Edge app window, taskbar pinning
(pinned shortcut may show as a separate taskbar button from the window), DPI fix for the file picker, new progress
Tracker with real models (ETA accuracy), timeline drag in Edge.
Found 2026-10-08: no `data\teksting.log` yet (shortcut launch probably never used), and a leftover
`Downloads\Kongens tale_tekstet.part.mp4` from 2026-10-07 17:37 — possibly an interrupted or failed burn-in.

## Open work (priority order)
1. **Verify the untested Windows items above**; read `data\teksting.log` for errors.
2. **CSV export in the colleague's exact format** — waiting for Jonas to get an example CSV. Don't guess the
   format; ask for the file. Likely Premiere-style (start/end timecodes + text), check delimiter, timecode
   format (frames vs ms), encoding (UTF-8 BOM for Excel?).
3. **Vimeo** — unknown whether the colleague uploads captions to Vimeo and whether we can get account access.
   Options: paste a Vimeo link to import the video; upload the finished track via Vimeo API. Ask first.
4. Collect real work-time numbers from 3–5 videos (Videoer page) and find where time goes.

## Conventions
- Keep it simple and functional; Jonas likes the current clean look (light page, dark editor "studio",
  brand blue `#1f4f78`, subtitle yellow `#ffd84d`).
- New UI text → `i18n.js` in nb + en. Check Norwegian plurals ("1 sted" vs "2 steder").
- Long-running work goes through `Worker`/`Exporter`, never in a request handler.
- After changing UI, test in fake mode at 1440×900 and a narrow (~400 px) window, light and dark.
- Never edit files in `data\` by hand while the app runs.
- Git: `.gitattributes` keeps `.bat/.vbs/.ps1/.txt` CRLF. Commit after each verified change.
