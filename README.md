# SaxScribe

SaxScribe turns a released recording, original demo, rehearsal recording, or already isolated horn stem into an editable saxophone part. Local mode runs on your Mac. Hosted mode uses an asynchronous Google Cloud Run Job. The AI-powered extra transcription check remains optional.

## What it does

1. Separates a wind/horn stem with local UVR, LALAL.AI API v1, or confidence-routed `auto`/`both` mode.
2. Runs Xavier Riley's sax-specific MIDI transcription command on the isolated WAV.
3. Estimates tempo, tuning and key from the original mix; removes fragments, enforces a monophonic line and chooses a conservative straight, swing-eighth, or triplet-eighth grid from the unquantized attacks.
4. Applies the selected saxophone's written transposition, octave-folds clearly impossible detector outliers into its normal range, and refuses any still-unplayable export.
5. Measures each candidate MIDI note against pYIN pitch frames and onset timing from the horn stem and original mix.
6. Scores each note from local pitch, voicing, attack, and relative wind-stem energy evidence. MusicXML can highlight medium-confidence notes in orange and low-confidence notes in red.
7. Optionally runs three AI phases: web research for the named recording, synchronized original/stem audio comparison, and structured synthesis across the audio evidence, raw/cleaned MIDI and draft MusicXML.
8. Returns Simple and Advanced MusicXML/MIDI versions with instrument-correct MIDI programs, dynamics, short phrase slurs, and optional PDFs when MuseScore is installed. Stems and evidence remain available as diagnostics.

The extra transcription check is off by default. When enabled, an invalid key, exhausted quota, missing model access, network request or response-validation failure stops the job instead of silently pretending that verification succeeded.

This is assisted transcription, not guaranteed publication-ready notation. Swing, scoops, growls, overlapping horns and separation artifacts still need a musician's review.

## Mac setup

Requirements: macOS, Python 3.10 or 3.11, Homebrew, Git and Node 20+.

```bash
cd SaxScribe-local
bash scripts/setup-mac.sh
```

Copy `.env.example` to `.env`. `OPENAI_API_KEY` is needed only if you enable the extra transcription check in the interface.
The startup script lets Python parse `.env`; it does not execute the file as shell code, so values such as `"Wind Inst"` are handled safely.

```bash
cp .env.example .env
# Optionally edit .env and add OPENAI_API_KEY for the extra transcription check.
bash scripts/start-local.sh
```

Open <http://localhost:5173>.

Setup downloads the exact 214 MB UVR wind checkpoint once into `.models/audio-separator/` and verifies its SHA-256 checksum. The checkpoint is not bundled in this ZIP.

## UVR model

The required model is `17_HP-Wind_Inst-UVR.pth`. Its Python stem name is `Woodwinds`, which is the same output that UVR's GUI calls **Wind Inst Only**. SaxScribe downloads that exact file from UVR's public model repository, verifies SHA-256 `acc6d472b4b478da9c9ab5af45b167749e05a7f65b30c7d5988b3700a513aeee`, writes only the `Woodwinds` stem, and includes `separation-manifest.json` as a diagnostic.

Every generated stem name is computed at runtime from the current upload and configured output label: `1_<uploaded input>_(<stem label>).wav`. No song title, recording, stem audio, key, tempo or transcription result is embedded in the application. The result screen shows the exact source filename beside an audio player.

The default separation parameters intentionally match the working UVR GUI setup: VR Architecture, WAV, window size 512, aggression 5, **Wind Inst Only**, GPU conversion off, Apple Silicon polyphase resampling, and high-end reconstruction enabled. Earlier builds incorrectly forced high-end reconstruction off and let the wrapper silently select Apple MPS, producing a materially different stem from the reference run. SaxScribe no longer falls back to another model. To download or re-verify the exact checkpoint without reinstalling dependencies, run:

```bash
bash scripts/download-wind-model.sh
```

Do not change `UVR_MODEL_NAME` unless you intentionally want to test a different separator. The app reports the configured model in `/api/health`.

## Separation providers

`SEPARATION_PROVIDER` controls the pipeline:

- `uvr`: use only the local `17_HP-Wind_Inst-UVR` checkpoint. This is the default for local use.
- `lalal`: use LALAL.AI API v1 with the Phoenix wind splitter.
- `auto`: run `SEPARATION_PRIMARY` first and run the other provider only if the candidate routing score is below `SEPARATION_QUALITY_THRESHOLD` or the primary fails.
- `both`: always run both configured providers and select the candidate with stronger local pitch/voicing evidence.

SaxScribe does not average the two audio waveforms. It transcribes each stem independently and compares the resulting note evidence. The reported routing score is a heuristic for choosing a candidate, not a claimed accuracy percentage. When both providers run, the evidence report includes their note-level agreement.

LALAL mode requires a business API key in `LALAL_API_KEY`. The key stays on the backend and is never sent to the browser.

## Optional direct horn stem

If UVR already produced a good `Wind Inst` WAV, choose **Already isolated horn** in the UI. This skips separation and avoids degrading the audio twice.

## Optional extra transcription check

When enabled, the review intentionally uses separate API calls:

1. The reasoning model researches the supplied song title/artist with web search.
2. `gpt-audio-1.5` receives synchronized chunks from the original mix and isolated horn for qualitative comparison.
3. The reasoning model receives the web memo, audio memos, original-mix analysis, pYIN note table, raw MIDI events, cleaned MIDI events and exact draft MusicXML, then returns schema-validated corrections.

Local pYIN pitch and onset measurements remain the numeric authority because a general audio-language model is not a sample-accurate sax pitch tracker. The audio model is used to find phrase-level discrepancies and propose corrections, but the backend enforces them: a missing note is inserted only when a stable pYIN span supports its pitch in a real gap; a large pitch or octave change requires pYIN support for the proposed pitch; and a long note is deleted only when horn evidence is weak or absent. Rejected AI suggestions are recorded in `evidence.json` and do not change the score.

By default audio is reviewed in 60-second chunks, up to eight chunks. Change `OPENAI_AUDIO_CHUNK_SECONDS` or `OPENAI_AUDIO_MAX_CHUNKS` in `.env` if necessary. Audio review consumes API credits. Synchronized review clips, derived measurements, MIDI data and MusicXML are sent to OpenAI; do not use this workflow for material you are not permitted to process.

## Output

Every completed job provides two versions:

- **Simple (recommended):** `sax-<instrument>-simple.musicxml` and `sax-simple-sounding.mid`. Straight performances use a coarse eighth-note grid; detected swing/triplet performances retain third-beat subdivisions. Tiny collisions are removed and adjacent repeated pitches are joined.
- **Advanced:** `sax-<instrument>-advanced.musicxml` and `sax-advanced-sounding.mid`. This uses the selected straight-sixteenth, swing-eighth, or triplet-eighth grid and adds velocity-derived dynamics and short, conservative phrase slurs.

If MuseScore is installed in its standard macOS location, SaxScribe also renders Simple and Advanced PDFs. PDF absence does not fail the job because hosted and headless environments may not include MuseScore. MIDI stays at sounding concert pitch, but its General MIDI program now matches the selected soprano, alto, tenor, or baritone saxophone.

The quantizer preserves a fractional leading-note offset and reports it as `pickup_offset_beats`. It does **not** claim reliable automatic downbeat/anacrusis detection; confirm pickup-bar placement in MuseScore.

Neither version requires the extra transcription check. Both contain the same range-validated concert pitches; the difference is rhythmic detail. MusicXML opens directly in MuseScore and preserves SaxScribe's notation choices. Importing MIDI makes MuseScore infer notation again, so MusicXML remains the preferred editable score format.

**Highlight uncertain notes** is enabled by default and does not use OpenAI. In both MusicXML files, black notes have strong local support, orange notes should be reviewed, and red notes have weak pitch/onset support or may be separation artifacts. The result screen reports counts for the Simple score, while `evidence.json` contains each Advanced note's score, flags, and human-readable reasons. MIDI cannot carry visible note colors, so its pitches and timing are unchanged.

SaxScribe does not claim that a highlighted note is a vocal. A stable sung note can look like a stable sax pitch to pYIN. The evidence report uses **possible non-horn leakage** only when the sound is unusually weak in the wind stem or a second configured separator does not confirm it.

B-flat tenor is written a major ninth above concert pitch. Before either draft or final MusicXML is written, SaxScribe octave-folds detector outliers into the normal written saxophone range and then refuses export if any impossible written pitch remains. These deterministic checks run whether AI review is enabled or not.

## Local output location

Each job is stored under `work/<job-id>/`, including a durable `job.json` status file. The browser remembers the latest job id, so a page refresh restores its progress or completed downloads. Completed jobs survive a backend restart; a job interrupted by a restart is marked failed rather than pretending to resume. Local queued/running jobs can be cancelled, and UVR plus the sax transcriber run in killable child processes. Both original and optional isolated-stem uploads use the same `MAX_UPLOAD_MB` cap. Jobs older than `KEEP_JOBS_HOURS` are deleted when the server starts.

## Limitations

- The sax transcription repository is alpha software and is installed from GitHub because it is not published on PyPI.
- Automatic major/minor decisions are unreliable when a song emphasizes the relative major or dominant. Confirm the proposed key in the interface.
- If the optional extra transcription check is enabled, it can fail because of an invalid key, exhausted quota, missing model access, network problems or service errors. Such failures stop the job and are not reported as successful verification.
- The exact UVR model weight is not redistributed. Check its terms separately before commercial deployment.

## Google Cloud hosting

The hosted architecture uses:

- Cloud Run service for the Vite/FastAPI website.
- Cloud Run Job for long-running audio processing.
- Cloud Storage for private inputs and outputs.
- Firestore for persistent job progress.
- Secret Manager for the LALAL business API key.

The same container serves the website and runs the worker. See [`deploy/gcp/README.md`](deploy/gcp/README.md). The provided deployment script defaults hosted separation to LALAL so no UVR weight or GPU is required in the production container.

The current hosted upload endpoint is capped at 30 MB. Browser-to-Cloud-Storage resumable uploads are still required before accepting large uncompressed WAV files in production.

## Development checks

```bash
source .venv/bin/activate
python -m compileall backend
python -m unittest discover -s tests
cd frontend && npm run build
```
