# SaxScribe

SaxScribe turns an uploaded recording, a permitted YouTube video, an original demo, a rehearsal recording, or an already isolated horn stem into an editable saxophone part. Local mode runs the free UVR workflow on your Mac. The hosted website offers Free (UVR, no AI) and Enhanced (one paid LALAL.AI separation plus a required AI evidence review) through Stripe Checkout.

## What it does

1. Accepts one uploaded audio file or one YouTube video link. YouTube URLs are normalized to a single video, capped by duration and file size, and downloaded as an audio-only source in the background.
2. Separates a wind/horn stem with UVR in Free or LALAL.AI API v1 in paid Enhanced.
3. Runs Xavier Riley's sax-specific MIDI transcription command on the isolated audio.
4. Estimates tempo, tuning and key from the original mix; removes clear fragments, enforces a monophonic line and chooses the coarsest straight, swing-eighth, or triplet-eighth grid that does not collapse distinct supported attacks.
5. Applies the selected saxophone's written transposition, octave-folds clearly impossible detector outliers into its normal range, and refuses any still-unplayable export.
6. Measures each candidate MIDI note against pYIN pitch frames and onset timing from the horn stem and original mix.
7. Scores each note from local pitch, voicing, attack, and relative wind-stem energy evidence. MusicXML can highlight medium-confidence notes in orange and low-confidence notes in red.
8. In Enhanced, runs a bounded two-pass review: recording-specific research and synchronized original/stem/rendered-score comparison, followed by one final Playable Lead Sheet comparison when a measurable mismatch remains.
9. Scores candidate interpretations and each AI proposal against globally assigned pitch/onset evidence and a lead-sheet complexity budget. Free/local compares adaptive and straight-grid deterministic spellings; Enhanced also scores its AI proposals. Unsupported or non-improving revisions are reverted, and rhythmic simplicity is not allowed to win merely by deleting supported notes.
10. Returns a recommended Playable Lead Sheet and a Detailed Performance in MusicXML/MIDI. The lead sheet keeps the original audio untouched, corrects small timing drift, removes only short weak artifacts when the audio evidence is sufficiently broad, and preserves distinct notes in dense runs. When evidence is sparse, notes remain visible and uncertain rather than being erased. The detailed version preserves more of the detected performance timing, dynamics, and short phrase slurs. The app includes a score preview, variable-speed horn-audio review, and optional PDFs when MuseScore is installed.

Free never calls LALAL.AI or OpenAI. Enhanced always uses both services; an invalid key, exhausted quota, missing model access, network request or response-validation failure stops the paid job instead of silently pretending that verification succeeded.

This is assisted transcription, not guaranteed publication-ready notation. Swing, scoops, growls, overlapping horns and separation artifacts still need a musician's review.

## Mac setup

Requirements: macOS, Python 3.10 or 3.11, Homebrew, Git and Node 22+.

```bash
cd SaxScribe-local
bash scripts/setup-mac.sh
```

Copy `.env.example` to `.env`. Local mode does not need Stripe, LALAL.AI, or OpenAI credentials.
The startup script lets Python parse `.env`; it does not execute the file as shell code, so values such as `"Wind Inst"` are handled safely.

```bash
cp .env.example .env
bash scripts/start-local.sh
```

Open <http://localhost:5173>.

The launcher starts the Python API first and does not start the browser UI until `/api/health` succeeds. If the backend exits or cannot become ready within 90 seconds, the launcher stops and saves the complete diagnostic output to `work/backend-startup.log` instead of leaving a disconnected frontend running.

Setup downloads the exact 214 MB UVR wind checkpoint once into `.models/audio-separator/` and verifies its SHA-256 checksum. The checkpoint is not bundled in this ZIP.

## YouTube input

Choose **YouTube link**, then paste the URL or drag a link from the browser into the field. SaxScribe accepts standard `youtube.com/watch`, `youtu.be`, Shorts, embed, and live-page URLs only when they resolve to one completed video. It deliberately rejects playlists, channels, searches, current/upcoming live streams, unknown-duration videos, and anything longer than `MAX_YOUTUBE_SECONDS` (eight minutes by default). The same `MAX_UPLOAD_MB` cap applies after download.

The link is normalized before it reaches the downloader, so it cannot be used as a general server-side URL fetcher. yt-dlp retrieves an audio-only stream in the local queue or Cloud Run worker. Video title and artist/uploader metadata fill blank song fields, but explicit user-entered values win. If YouTube blocks a cloud IP, requires sign-in, or changes its extraction protocol, the job stops and asks for a direct audio upload instead of claiming success.

YouTube currently requires yt-dlp to run JavaScript challenge code, so SaxScribe installs the matching `yt-dlp-ejs` package and uses Node 22+. This dependency needs regular updates when YouTube changes. The feature is a technical convenience, not permission to copy content. The UI requires the user to confirm authorization under YouTube's terms and applicable law. That confirmation does not create a license; a commercial deployment should receive legal review before enabling `YOUTUBE_INPUT_ENABLED=true`.

## UVR model

The required model is `17_HP-Wind_Inst-UVR.pth`. Its Python stem name is `Woodwinds`, which is the same output that UVR's GUI calls **Wind Inst Only**. SaxScribe downloads that exact file from UVR's public model repository, verifies SHA-256 `acc6d472b4b478da9c9ab5af45b167749e05a7f65b30c7d5988b3700a513aeee`, writes only the `Woodwinds` stem, and includes `separation-manifest.json` as a diagnostic.

Every generated stem name is computed at runtime from the current upload and configured output label: `1_<uploaded input>_(<stem label>).wav`. No song title, recording, stem audio, key, tempo or transcription result is embedded in the application. The result screen shows the exact source filename beside an audio player.

The default separation parameters intentionally match the working UVR GUI setup: VR Architecture, WAV, window size 512, aggression 5, **Wind Inst Only**, GPU conversion off, Apple Silicon polyphase resampling, and high-end reconstruction enabled. Earlier builds incorrectly forced high-end reconstruction off and let the wrapper silently select Apple MPS, producing a materially different stem from the reference run. SaxScribe no longer falls back to another model. To download or re-verify the exact checkpoint without reinstalling dependencies, run:

```bash
bash scripts/download-wind-model.sh
```

Do not change `UVR_MODEL_NAME` unless you intentionally want to test a different separator. Exact model and tuning details stay in server configuration and logs; the customer UI and public health response expose only whether the standard and Enhanced workflows are ready.

## Separation providers

`SEPARATION_PROVIDER` remains available for development and direct pipeline tests:

- `uvr`: use only the local `17_HP-Wind_Inst-UVR` checkpoint. This is the default for local use.
- `lalal`: use LALAL.AI API v1 with the Phoenix wind splitter.
- `auto`: run `SEPARATION_PRIMARY` first and run the other provider only if the candidate routing score is below `SEPARATION_QUALITY_THRESHOLD` or the primary fails.
- `both`: always run both configured providers and select the candidate with stronger local pitch/voicing evidence.

SaxScribe does not average the two audio waveforms. It transcribes each stem independently and compares the resulting note evidence. The reported routing score is a heuristic for choosing a candidate, not a claimed accuracy percentage. When both providers run, the evidence report includes their note-level agreement.

The product API does not trust the browser's provider choice. Local and hosted Free are forced to `uvr`; hosted Enhanced is forced to `lalal` and AI review is forced on. LALAL mode requires a business API key in `LALAL_API_KEY`. The key stays on the backend and is never sent to the browser.

## Optional direct horn stem

If UVR already produced a good `Wind Inst` WAV, choose **Already isolated horn** in the UI. This skips separation and avoids degrading the audio twice.

## Enhanced AI evidence review

Every paid Enhanced transcription intentionally uses separate review stages:

1. The reasoning model researches the supplied song title/artist with web search.
2. `gpt-audio-1.5` receives synchronized chunks from the original mix, isolated horn and a neutral synthesis of the current symbolic score for qualitative comparison.
3. The reasoning model receives the web memo, audio memos, original-mix analysis, local note table, raw MIDI events, cleaned MIDI events, exact draft MusicXML and an objective mismatch report, then returns schema-validated corrections.
4. If the resulting Playable Lead Sheet still has a strong measurable mismatch, the rendered lead sheet receives one final review. A local scorer retains that revision only if audio agreement improves without materially increasing notation complexity.

Local pYIN pitch and onset measurements remain the numeric authority because a general audio-language model is not a sample-accurate sax pitch tracker. The audio model is used to find phrase-level discrepancies and propose corrections, but the backend enforces them: a missing note is inserted only when a stable pYIN span supports its pitch in a real gap; a large pitch or octave change requires pYIN support for the proposed pitch; and a long note is deleted only when horn evidence is weak or absent. Rejected AI suggestions are recorded in `evidence.json` and do not change the score.

The feedback loop is deliberately bounded at two AI passes. Candidate notes are assigned globally to measured horn pitch spans with SciPy's Hungarian assignment so two nearby score notes cannot reuse one detected attack. This borrows one assignment principle from [Snapping Matters](https://arxiv.org/abs/2606.11903); SaxScribe does not implement that paper's neural posteriorgram, alignment-training pipeline, or transcriber training. The original audio measurements act as an external anchor, but SaxScribe does not implement the cycle-consistent training system, unpaired corpora, or model fine-tuning from [Music Transcription with (Almost) No Supervision](https://arxiv.org/abs/2605.24193). These are limited engineering adaptations, not implementations of either paper. No paper code or model weights are copied.

Each candidate records pitch agreement, onset agreement, duration overlap, supported-note precision, measured-span recall and a separate readability penalty. The penalty increases for excessive density, sub-eighth fragments, off-eighth timing, tuplets and micro-rests. A pass is rejected when its objective improvement is negligible, its audio agreement falls, its complexity increases beyond the budget, or the local audio anchor is insufficient. These are engineering heuristics, not a claimed research benchmark; they require calibration against musician-corrected sax examples.

By default audio is reviewed in 60-second chunks, up to eight chunks. Change `OPENAI_AUDIO_CHUNK_SECONDS` or `OPENAI_AUDIO_MAX_CHUNKS` in hosted configuration if necessary. Synchronized review clips, derived measurements, MIDI data and MusicXML are sent to OpenAI; the source recording is also sent to LALAL.AI for separation. Do not use Enhanced for material you are not permitted to process.

## Output

Every completed job provides two interpretations:

- **Playable Lead Sheet (recommended):** `sax-<instrument>-playable-lead-sheet.musicxml` and `sax-playable-lead-sheet.mid`. It uses raw, pre-quantization attack timing as evidence but does not alter the recording. Small drift snaps to the coarsest standard grid that preserves distinct supported attacks. Dense sixteenth-note runs, triplets, syncopations and repeated-pitch reattacks remain when supported; only short weak artifacts are eligible for removal, and only when the surrounding evidence is sufficiently reliable. Sparse evidence produces review warnings instead of destructive melody reduction.
- **Detailed Performance:** `sax-<instrument>-detailed-performance.musicxml` and `sax-detailed-performance.mid`. This preserves the detected straight-sixteenth, swing-eighth, or triplet-eighth timing and adds velocity-derived dynamics and short, conservative phrase slurs.

If MuseScore is installed in its standard macOS location, SaxScribe also renders Playable Lead Sheet and Detailed Performance PDFs. PDF absence does not fail the job because hosted and headless environments may not include MuseScore. MIDI stays at sounding concert pitch, but its General MIDI program matches the selected soprano, alto, tenor, or baritone saxophone.

The completed-job panel includes **View generated sheet music**. It renders either interpretation directly in the browser, lets the user switch between Lead Sheet and Performance, and provides zoom and direct MusicXML download without requiring MuseScore or a third-party CDN. Enhanced results also state whether the second pass was run and whether a revision cleared the quality/readability gates. The notation renderer is loaded only when the viewer opens so it does not inflate the app's initial download. This preview is intended for review; the downloadable MusicXML remains the editable source of truth.

Before MusicXML export, SaxScribe converts rounded timing values to exact quarter-beat or third-beat fractions and splits longer composite durations into tied notation. Notes are split across barlines before gap-filling rests are added, and every generated measure is checked for the expected length. This prevents decimal approximations such as `0.3333` from becoming durations that MusicXML cannot express and prevents invisible rests from making tied measures overfull.

Triplet durations remain encoded with exact MusicXML `time-modification` values. SaxScribe omits music21's optional explicit tuplet-bracket spans because sparse detected passages can produce incomplete visual groups that MuseScore reports as a corrupt file. This changes only the bracket markup, not playback timing. When no artist is supplied, the export also removes music21's default composer credit.

The final notation timeline is also forced to remain monophonic after quantization: competing attacks on the same grid point are de-duplicated, and a note that extends into the following attack is clipped at that attack. This prevents triplet rounding from creating a hidden second voice and an overfull MusicXML measure. If a local background job still fails, the complete traceback is printed in the `start-local.sh` terminal and saved privately as `work/<job-id>/error.log`.

The quantizer preserves a fractional leading-note offset and reports it as `pickup_offset_beats`. It does **not** claim reliable automatic downbeat/anacrusis detection; confirm pickup-bar placement in MuseScore.

Playable Lead Sheet and Detailed Performance are notation interpretations inside either product plan; they are not the Free and Enhanced payment plans. Both contain range-validated concert pitches, although the lead sheet can omit weak fragments and combine repeated notes. MusicXML opens directly in MuseScore and preserves SaxScribe's notation choices. Importing MIDI makes MuseScore infer notation again, so MusicXML remains the preferred editable score format.

**Highlight uncertain notes** is enabled by default and does not use OpenAI. In both MusicXML files, black notes have strong local support, orange notes should be reviewed, and red notes have weak pitch/onset support or may be separation artifacts. The result screen reports counts for the lead sheet, while `evidence.json` contains each detailed-performance note's score, flags, and human-readable reasons. MIDI cannot carry visible note colors, so its pitches and timing are unchanged.

SaxScribe does not claim that a highlighted note is a vocal. A stable sung note can look like a stable sax pitch to pYIN. The evidence report uses **possible non-horn leakage** only when the sound is unusually weak in the wind stem or a second configured separator does not confirm it.

B-flat tenor is written a major ninth above concert pitch. Before either draft or final MusicXML is written, SaxScribe octave-folds detector outliers into the normal written saxophone range and then refuses export if any impossible written pitch remains. These deterministic checks run whether AI review is enabled or not.

## Local output location

Each job is stored under `work/<job-id>/`, including a durable `job.json` status file. The browser remembers the latest job id, so a page refresh restores its progress or completed downloads. Completed jobs survive a backend restart; a job interrupted by a restart is marked failed rather than pretending to resume. The interface does not show a cancel-job control. Backend cancellation remains available for controlled process shutdown, and UVR plus the sax transcriber still run in killable child processes. Both original and optional isolated-stem uploads use the same `MAX_UPLOAD_MB` cap. Jobs older than `KEEP_JOBS_HOURS` are deleted when the server starts.

## Limitations

- The sax transcription repository is alpha software and is installed from GitHub because it is not published on PyPI.
- Automatic major/minor decisions are unreliable when a song emphasizes the relative major or dominant. Confirm the proposed key in the interface.
- Enhanced can fail because of an invalid service key, exhausted quota, missing model access, payment-verification error, network problem or service error. Such failures stop the job and are not reported as successful verification.
- YouTube input is best-effort. Private, members-only, age-restricted, region-blocked and bot-challenged videos may fail; upload an authorized local audio copy instead. SaxScribe does not accept account cookies or YouTube credentials.
- The exact UVR model weight is not redistributed. Check its terms separately before commercial deployment.

## Google Cloud hosting

Create one active, non-recurring Stripe Price for **SaxScribe Enhanced**. The amount lives in Stripe, not in this codebase. Set `STRIPE_ENHANCED_PRICE_ID` to its `price_...` id and store the Stripe secret key in Google Secret Manager. The server creates a Stripe-hosted Checkout Session, retrieves the returned Session server-side, verifies that it is paid and matches the configured Price, and atomically allows that Checkout Session to start only one Enhanced job. A completed job consumes the purchase; a worker-handled failure releases it for a retry.

The browser never decides which paid features run. The backend derives the package:

- `free` → UVR, AI off, no payment.
- `enhanced` → paid Session required, LALAL.AI, AI review required, pre-isolated uploads rejected.
- local runtime → `free` only.

An optional `/api/billing/webhook` endpoint verifies Stripe signatures when `STRIPE_WEBHOOK_SECRET` is configured. Synchronous paid-Session verification remains the job-creation gate.

The hosted architecture uses:

- Cloud Run service for the Vite/FastAPI website.
- Cloud Run Job for long-running audio processing.
- Cloud Storage for private inputs and outputs.
- Firestore for persistent job progress.
- Stripe Checkout for one-time Enhanced purchases.
- yt-dlp plus Node 22 for optional single-video YouTube input.
- Secret Manager for LALAL.AI, OpenAI and Stripe server keys.

The same container serves the website and runs the worker. See [`deploy/gcp/README.md`](deploy/gcp/README.md). The container build downloads and checksum-verifies the exact UVR wind model for hosted Free. Per-job metadata sends Free to UVR and paid Enhanced to LALAL.AI plus AI review.

The current hosted upload endpoint is capped at 30 MB. Browser-to-Cloud-Storage resumable uploads are still required before accepting large uncompressed WAV files in production.

## Development checks

```bash
source .venv/bin/activate
python -m compileall backend
python -m unittest discover -s tests
cd frontend && npm run build
```
