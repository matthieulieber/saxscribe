# Reference recording workflow

In the local app, open **Reference recordings** below the transcription panels.
Choose a saved case, listen to its recording, open its PDF, and click **Use this
recording for transcription**. Then create the score normally.

Add a verified, single-melody MIDI exported from the matching score. Choose
concert pitch for normal sounding-pitch MIDI exports; choose written pitch only
if the MIDI note numbers literally match the transposed printed notes. A tenor
written C5 is converted to sounding Bb3. Check the MIDI against the PDF before
confirming verification. Export ties as sustained notes and omit accompaniment.

After transcription completes, select the same reference and click **Compare
current transcription**. The app reports exact pitch matches, substitutions,
missing notes, extra notes, precision and recall for both output versions. Note
differences are available below the table. Comparisons are saved as JSON beside
the reference files in `work/references`, which is excluded from job expiration.
Override this location with `REFERENCE_DIR` if needed.

These metrics compare pitch sequences only. They do not score tempo, rhythm,
rests, articulation, engraving, or underlying model quality across other pieces.
Ambiguous repeated patterns can admit multiple alignments. No model weights or
transcription parameters are changed automatically.

The private downloadable package includes the user's Minor Blues Etude recording
and Nathan Hassall PDF as a pending reference. Verified MIDI is not included:
the printed notes have not yet been independently digitized and checked. The
audio/PDF are excluded from the public repository.

For the packaged app, unzip into a new folder, then run:

```bash
bash scripts/setup-mac.sh
bash scripts/start-local.sh
```

Open http://127.0.0.1:5173. Existing model files and `.env` settings are not
included in the package. This feature is available only in local mode.
