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
  `TEKSTING_IDLE_EXIT` (seconds, default 90, browser fallback only), `TEKSTING_DEBUG_PORT` (dev: Chrome DevTools
  port for the app window, e.g. 9333 → http://127.0.0.1:9333/json; drive it with a CDP script).
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
| `server.py` | API, background `Worker` (transcription queue, one job at a time), `Exporter` (burn-in queue), `Tracker` (phase-weighted progress + ETA), app mode (own window via pywebview/WebView2, see below; Chrome/Edge `--app` only as fallback), Windows keep-awake while busy, tkinter file picker in a subprocess (DPI-aware) |
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

### App window (app mode, since 2026-10-08)
`run_window` in `server.py`: pywebview (`gui="edgechromium"`, WebView2 is built into Windows) owns the main
thread, uvicorn runs in a thread beside it. Process AppUserModelID `Teksting.Teksting` = the ID that
`lag-snarveier.ps1` writes on the shortcuts → one taskbar button with the Teksting icon, and pinning works.
(Replaced the Chrome/Edge `--app` window: it showed the Chrome icon and its pin just opened Chrome.)
- Closing the window when idle quits at once. Closing while a job runs only **hides** it; the watchdog quits
  when the last job is done. Starting again (shortcut) while running → `POST /api/show` brings the window back.
- `Teksting.vbs` must start pythonw with window style 1, not 0: Windows applies the "hidden" start to the
  first window. `run_window` also re-shows on the `shown` event, as a safety net.
- Startup timings go to the log ("server ready after", "window shown after"); ~4 s on Jonas's PC.
  Two traps found: the nb dictionary takes ~10 s of pure Python to load (now `warm_speller`, after the
  window is up), and on Windows a connect to a closed port takes ~2 s (now `port_in_use`, 0.3 s timeout).
- WebView2 with pywebview's defaults: no browser right-click menu, no browser shortcuts (Ctrl+F reaches the
  app). Downloads ("Last ned SRT") show a Save dialog (`ALLOW_DOWNLOADS`).

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

### Performance (measured 2026-10-08, Ryzen 5 5625U, 6 cores / 12 threads)
- `cpu_threads=physical_cores()` (4–8): large model 80.5 s → 59.9 s per 60 s of speech vs the default 4
  threads, identical text. 12 threads (hyper-threads) is *slower*. Small model gains nothing.
- Large ≈ 1× real time on this PC; a whole job (large + rescue + small double-check) ≈ 1.7× real time.
- `Engine._loaded` keeps large + small loaded (~2 GB RAM): no reload per video in a queue (−20 s/video).
- `Engine.audio()` decodes each file once per job (bit-identical to the old per-step decode, verified);
  waveform via `peaks_from_audio` (correlation 0.999 with `compute_peaks`, which is still the fallback).
- `Store` caches meta.json in memory (all writes go through it): `/api/state` with 60 videos was 71 ms,
  polled every second. Don't edit meta.json by hand while the app runs.

### Burn-in cleanup (fixed 2026-10-08)
`burn.burn` always removes its temp folder and `.part.mp4` (done, cancelled, error). Deleting a project stops
its transcription *and* burn-in first. Quit calls `burn.abort_all()` (kills ffmpeg; the burn-in resumes on the
next start). `Store.cleanup()` at startup removes project folders without meta.json and stale `burn\` folders.
Worker/Exporter use `safe_update` and catch `KeyError`, so a project deleted mid-job can't kill the thread
(before: one delete during burn-in broke all burn-ins until restart).

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
**Verified on Windows 2026-10-08** (fake mode, via CDP on the real window): own window opens, video plays,
editor works, close-while-busy hides + auto-quit, second start re-shows, idle close quits, spell check.
**Not yet verified on Windows**: pinning from the real shortcut (by Jonas), DPI of the file picker, SRT download
Save dialog, new progress Tracker with real models (ETA accuracy), timeline drag.
Open (2026-10-08):
- Double-check flags varied between two app jobs on the *same* audio (4 vs 8 flags) although main-model words
  were identical. Not reproducible outside the server: small model is deterministic alone, after large, and
  through `Engine.transcribe` (also temperature fallback never triggered). Suspect something in the server job
  (hotwords/prompt? thread timing?). Flags are only review hints; subtitles come from the main model.
- i18n `offline` text still says "black window / start.bat", which no longer fits app mode.
- `Downloads\Kongens tale_tekstet.part.mp4` (old junk from before the fix) — Jonas to delete.

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
- Python scripts that rewrite files: `write_text(..., newline="
")` (Windows otherwise writes CRLF).
- Git: `.gitattributes` keeps `.bat/.vbs/.ps1/.txt` CRLF. Commit after each verified change.
