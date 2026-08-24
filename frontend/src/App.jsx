import { useEffect, useMemo, useState } from 'react'
import {
  ArrowDownToLine,
  AudioLines,
  Check,
  CircleAlert,
  FileAudio,
  FileMusic,
  LoaderCircle,
  Music2,
  Search,
  SlidersHorizontal,
  Sparkles,
  Square,
  UploadCloud,
} from 'lucide-react'

const STAGES = [
  { id: 'analyzing', label: 'Read the recording', icon: AudioLines },
  { id: 'separating', label: 'Isolate the horn', icon: SlidersHorizontal },
  { id: 'transcribing', label: 'Transcribe sax MIDI', icon: Music2 },
  { id: 'comparing', label: 'Measure audio/MIDI agreement', icon: AudioLines },
  { id: 'cleaning', label: 'Build deterministic draft', icon: Sparkles },
  { id: 'reviewing', label: 'Run the extra transcription check', icon: Search },
  { id: 'exporting', label: 'Build the score', icon: FileMusic },
]

function FilePicker({ id, label, hint, file, onChange, optional = false }) {
  const [dragging, setDragging] = useState(false)
  const accept = 'audio/*,.wav,.mp3,.m4a,.aif,.aiff,.flac'
  const choose = (list) => list?.[0] && onChange(list[0])
  return (
    <div className="file-field">
      <div className="field-heading">
        <label htmlFor={id}>{label}</label>
        {optional && <span>optional</span>}
      </div>
      <label
        className={`dropzone ${dragging ? 'dragging' : ''} ${file ? 'has-file' : ''}`}
        onDragOver={(event) => { event.preventDefault(); setDragging(true) }}
        onDragLeave={() => setDragging(false)}
        onDrop={(event) => { event.preventDefault(); setDragging(false); choose(event.dataTransfer.files) }}
      >
        <input id={id} type="file" accept={accept} onChange={(event) => choose(event.target.files)} />
        <span className="drop-icon">{file ? <FileAudio size={22} /> : <UploadCloud size={22} />}</span>
        <span className="drop-copy">
          <strong>{file ? file.name : 'Choose or drop audio'}</strong>
          <small>{file ? `${(file.size / 1024 / 1024).toFixed(1)} MB` : hint}</small>
        </span>
        {file && <span className="file-check"><Check size={16} /></span>}
      </label>
    </div>
  )
}

function App() {
  const [health, setHealth] = useState(null)
  const [original, setOriginal] = useState(null)
  const [isolated, setIsolated] = useState(null)
  const [title, setTitle] = useState('')
  const [artist, setArtist] = useState('')
  const [instrument, setInstrument] = useState('tenor')
  const [useAi, setUseAi] = useState(false)
  const [highlightUncertain, setHighlightUncertain] = useState(true)
  const [job, setJob] = useState(null)
  const [error, setError] = useState('')

  useEffect(() => {
    fetch('/api/health').then((response) => response.json()).then(setHealth).catch(() => setHealth({ ok: false }))
    const savedJobId = window.localStorage.getItem('saxscribe-active-job')
    if (savedJobId) {
      fetch(`/api/jobs/${savedJobId}`)
        .then((response) => response.ok ? response.json() : Promise.reject(new Error('expired')))
        .then(setJob)
        .catch(() => window.localStorage.removeItem('saxscribe-active-job'))
    }
  }, [])

  useEffect(() => {
    if (job?.id) window.localStorage.setItem('saxscribe-active-job', job.id)
  }, [job?.id])

  useEffect(() => {
    if (!job?.id || ['complete', 'error', 'cancelled'].includes(job.status)) return undefined
    const timer = setInterval(async () => {
      try {
        const response = await fetch(`/api/jobs/${job.id}`)
        const next = await response.json()
        setJob(next)
      } catch {
        setError('The processing server stopped responding.')
      }
    }, 1500)
    return () => clearInterval(timer)
  }, [job?.id, job?.status])

  const stages = useMemo(() => (job?.use_ai ?? useAi) ? STAGES : STAGES.filter((stage) => stage.id !== 'reviewing'), [job?.use_ai, useAi])

  const currentIndex = useMemo(() => {
    if (!job) return -1
    const aliases = { complete: stages.length }
    return aliases[job.stage] ?? stages.findIndex((stage) => stage.id === job.stage)
  }, [job, stages])

  async function submit(event) {
    event.preventDefault()
    setError('')
    if (!original) {
      setError('Add the original recording first. It supplies tempo and harmony evidence; downbeat placement still needs review.')
      return
    }
    const data = new FormData()
    data.append('original', original)
    if (isolated) data.append('isolated', isolated)
    data.append('title', title)
    data.append('artist', artist)
    data.append('instrument', instrument)
    data.append('use_ai', String(useAi))
    data.append('highlight_uncertain', String(highlightUncertain))
    setJob({ status: 'uploading', stage: 'queued', percent: 2, message: 'Uploading the recording' })
    try {
      const response = await fetch('/api/jobs', { method: 'POST', body: data })
      const payload = await response.json()
      if (!response.ok) throw new Error(payload.detail || 'The job could not start.')
      setJob({ ...payload, status: 'queued', stage: 'queued', percent: 3, message: 'Waiting for the transcription worker' })
    } catch (reason) {
      setError(reason.message)
      setJob(null)
    }
  }

  async function cancelJob() {
    if (!job?.id) return
    try {
      const response = await fetch(`/api/jobs/${job.id}/cancel`, { method: 'POST' })
      const payload = await response.json()
      if (!response.ok) throw new Error(payload.detail || 'The job could not be cancelled.')
      setJob(payload)
    } catch (reason) {
      setError(reason.message)
    }
  }

  function resetJob() {
    window.localStorage.removeItem('saxscribe-active-job')
    setJob(null)
  }

  const busy = job && !['complete', 'error', 'cancelled'].includes(job.status)
  const facts = job?.result?.facts
  const windFile = job?.result?.files?.find((file) => file.role === 'wind_stem')
  const simpleScoreFile = job?.result?.files?.find((file) => file.role === 'musicxml')
  const simpleMidiFile = job?.result?.files?.find((file) => file.role === 'simple_midi')
  const advancedScoreFile = job?.result?.files?.find((file) => file.role === 'advanced_musicxml')
  const advancedMidiFile = job?.result?.files?.find((file) => file.role === 'advanced_midi')
  const simplePdfFile = job?.result?.files?.find((file) => file.role === 'simple_pdf')
  const advancedPdfFile = job?.result?.files?.find((file) => file.role === 'advanced_pdf')
  const simpleConfidence = job?.result?.note_confidence?.simple
  const featuredRoles = new Set(['musicxml', 'simple_midi', 'advanced_musicxml', 'advanced_midi', 'simple_pdf', 'advanced_pdf'])
  const advancedFiles = job?.result?.files?.filter((file) => !featuredRoles.has(file.role)) || []

  return (
    <main>
      <header className="topbar">
        <a className="brand" href="#top" aria-label="SaxScribe home">
          <span className="brand-mark"><Music2 size={19} /></span>
          <span>SaxScribe</span>
          <em>PRIVATE</em>
        </a>
        <div className={`server-pill ${health?.ok ? 'online' : ''}`}>
          <span /> {health === null ? 'Checking engine' : !health.ok ? 'Engine offline' : `${health.runtime_mode === 'gcp' ? 'Hosted' : 'Local'} engine · build ${health.build}`}
        </div>
      </header>

      <section className="hero" id="top">
        <p className="eyebrow">Audio → editable notation</p>
        <h1>Turn a sax line into<br /><i>sheet music you can use.</i></h1>
        <p className="lede">Separate the horn, transcribe its pitches, and export an editable draft. Add an optional second check for noisy or complex recordings.</p>
        <div className="pipeline-line" aria-label="Processing stages">
          <span>Full mix</span><b>01</b><span>Horn stem</span><b>02</b><span>Clean MIDI</span><b>03</b><span>Score</span>
        </div>
      </section>

      <section className="workspace">
        <form className="panel form-panel" onSubmit={submit}>
          <div className="panel-title">
            <span>01</span>
            <div><h2>Source material</h2><p>The full mix anchors rhythm and key. Add a clean UVR stem if you already have one.</p></div>
          </div>

          <FilePicker id="original" label="Original recording" hint="WAV, M4A, MP3, AIFF or FLAC" file={original} onChange={setOriginal} />
          <FilePicker id="isolated" label="Already isolated horn" hint="Skip UVR and avoid separating twice" file={isolated} onChange={setIsolated} optional />
          {isolated && <p className="warning"><CircleAlert size={15} /> UVR separation will be skipped because an isolated-horn file is selected.</p>}
          {!isolated && health?.separation_provider === 'uvr' && health?.uvr_model && (
            <p className="metadata-note">
              <SlidersHorizontal size={14} /> {health.uvr_model.replace(/\.pth$/i, '')} · {health.uvr_model_ready ? 'checkpoint verified locally' : 'checkpoint missing'} · {health.uvr_output_label || health.uvr_target_stem} Only ({health.uvr_target_stem}) · VR {health.uvr_vr_window_size} · aggression {health.uvr_vr_aggression} · high-end restoration {health.uvr_vr_high_end_process ? 'on' : 'off'} · {health.uvr_device} / {health.uvr_resampler} · WAV
            </p>
          )}
          {!isolated && health?.separation_provider !== 'uvr' && health?.separation_provider && (
            <p className="metadata-note"><SlidersHorizontal size={14} /> Separation mode: {health.separation_provider.toUpperCase()} · primary {health.separation_primary.toUpperCase()} · LALAL {health.lalal_ready ? 'configured' : 'not configured'} · UVR {health.uvr_model_ready ? 'ready' : 'not installed'}</p>
          )}
          {!isolated && health?.separation_ready === false && <p className="error"><CircleAlert size={15} /> No configured separator is ready. Install the UVR wind model or configure LALAL_API_KEY for the selected separation mode.</p>}

          <div className="field-grid">
            <label>Song title<input value={title} onChange={(event) => setTitle(event.target.value)} placeholder="Optional song title" /></label>
            <label>Artist<input value={artist} onChange={(event) => setArtist(event.target.value)} placeholder="Optional artist" /></label>
          </div>
          <p className="metadata-note"><Search size={14} /> Song details help cross-check tempo, key and meter. The uploaded audio remains authoritative.</p>

          <fieldset>
            <legend>Write the part for</legend>
            <div className="instrument-grid">
              {[
                ['tenor', 'B♭ Tenor'], ['alto', 'E♭ Alto'], ['baritone', 'E♭ Bari'], ['soprano', 'B♭ Soprano'], ['concert', 'Concert'],
              ].map(([value, label]) => (
                <label className={instrument === value ? 'selected' : ''} key={value}>
                  <input type="radio" name="instrument" value={value} checked={instrument === value} onChange={() => setInstrument(value)} />
                  {label}
                </label>
              ))}
            </div>
          </fieldset>

          <label className="toggle-row">
            <span><strong>Highlight uncertain notes <em>No AI needed</em></strong><small>Colors medium-confidence notes orange and low-confidence notes red in MusicXML. MIDI pitches are unchanged. A color is a review warning, not proof that the note is wrong.</small></span>
            <input type="checkbox" checked={highlightUncertain} disabled={busy} onChange={(event) => setHighlightUncertain(event.target.checked)} />
          </label>

          <label className="toggle-row">
            <span><strong>Extra transcription check <em>Uses AI</em></strong><small>Compares the original, horn stem, and score. It can add a missing note or repair an octave only when local pYIN measurements support that change; unsupported suggestions remain unchanged. It also cross-checks key and tempo. Adds processing time and API cost; audio excerpts are sent to OpenAI.</small></span>
            <input type="checkbox" checked={useAi} disabled={busy} onChange={(event) => setUseAi(event.target.checked)} />
          </label>
          {useAi && health && !health.ai_ready && <p className="warning"><CircleAlert size={15} /> The extra check needs an OPENAI_API_KEY. Turn it off to avoid OpenAI; the selected separator behavior is unchanged.</p>}

          {error && <p className="error"><CircleAlert size={17} />{error}</p>}
          <button className="primary" disabled={busy || health?.ok === false || (!isolated && health?.separation_ready === false) || (useAi && health?.ai_ready === false)}>
            {busy ? <><LoaderCircle className="spin" size={18} /> Processing…</> : <><Sparkles size={18} /> Create sax score</>}
          </button>
        </form>

        <aside className="panel result-panel">
          <div className="panel-title compact">
            <span>02</span>
            <div><h2>Processing desk</h2><p>Follow the evidence, not a black box.</p></div>
          </div>

          {!job && (
            <div className="empty-state">
              <div className="staff">𝄞</div>
              <h3>Your score will appear here</h3>
              <p>Start with a 20–40 second passage. You can judge pitch and rhythmic accuracy before processing a whole song.</p>
            </div>
          )}

          {job && !['complete', 'error', 'cancelled'].includes(job.status) && (
            <div className="progress-area">
              <div className="progress-copy"><strong>{job.message}</strong><span>{job.percent || 0}%</span></div>
              <div className="progress-track"><span style={{ width: `${job.percent || 0}%` }} /></div>
              <div className="stage-list">
                {stages.map((stage, index) => {
                  const Icon = stage.icon
                  const done = index < currentIndex
                  const active = index === currentIndex
                  return <div className={`${done ? 'done' : ''} ${active ? 'active' : ''}`} key={stage.id}>
                    <span>{done ? <Check size={16} /> : active ? <LoaderCircle className="spin" size={16} /> : <Icon size={16} />}</span>
                    <p>{stage.label}</p>
                  </div>
                })}
              </div>
              <p className="patient-note">Processing input: <b>{job.source_name || original?.name}</b>{job.isolated_source_name ? ` · UVR skipped in favor of ${job.isolated_source_name}` : ''}</p>
              {health?.runtime_mode === 'local' && <button className="cancel-button" type="button" disabled={job.status === 'cancelling'} onClick={cancelJob}><Square size={12} /> {job.status === 'cancelling' ? 'Stopping…' : 'Cancel job'}</button>}
            </div>
          )}

          {job?.status === 'error' && (
            <div className="failure-state"><CircleAlert size={28} /><h3>Processing stopped</h3><p>{job.error || job.message}</p><button onClick={resetJob}>Return to setup</button></div>
          )}

          {job?.status === 'cancelled' && (
            <div className="failure-state"><Square size={28} /><h3>Processing cancelled</h3><p>The active local process was stopped. Uploaded files remain only until the normal job cleanup window.</p><button onClick={resetJob}>Return to setup</button></div>
          )}

          {job?.status === 'complete' && (
            <div className="complete-state">
              <div className="success-title"><span><Check size={20} /></span><div><h3>Two drafts ready</h3><p>{job.result.simple_notes ?? job.result.notes} simple notes · {job.result.advanced_notes ?? job.result.notes} detailed notes</p></div></div>
              <div className="facts-grid">
                <div><small>Concert key</small><strong>{facts.concert_key} {facts.mode}</strong></div>
                <div><small>Tempo</small><strong>{Math.round(facts.bpm)} BPM</strong></div>
                <div><small>Meter</small><strong>{facts.meter}</strong></div>
                <div><small>Part</small><strong>{(job.instrument || instrument) === 'concert' ? 'Concert' : (job.instrument || instrument)[0].toUpperCase() + (job.instrument || instrument).slice(1)}</strong></div>
                <div><small>Stem quality</small><strong>{job.result.confidence?.level || 'Review'} </strong></div>
                <div><small>Separator</small><strong>{(job.result.selected_provider || 'uploaded').toUpperCase()}</strong></div>
              </div>
              {simpleConfidence && <div className="note-confidence">
                <div className="confidence-heading"><strong>Simple score confidence</strong><small>{simpleConfidence.uncertain} notes to review</small></div>
                <div className="confidence-counts">
                  <span><i className="confidence-dot high" />{simpleConfidence.high} high</span>
                  <span><i className="confidence-dot medium" />{simpleConfidence.medium} review</span>
                  <span><i className="confidence-dot low" />{simpleConfidence.low} weak</span>
                </div>
                {simpleConfidence.total > 0 && simpleConfidence.low / simpleConfidence.total >= 0.6 && (
                  <p className="confidence-failure"><CircleAlert size={13} /> Most notes lack support from the horn stem. Do not trust this transcription yet; check that the correct horn-only file was used.</p>
                )}
                {simpleConfidence.possible_non_horn > 0 && <p><CircleAlert size={13} /> {simpleConfidence.possible_non_horn} {simpleConfidence.possible_non_horn === 1 ? 'note has' : 'notes have'} possible leakage or non-horn evidence. This does not identify vocals conclusively.</p>}
                <small>{job.result.highlight_uncertain_notes ? 'The same orange/red flags are embedded in both MusicXML scores.' : 'Score coloring was disabled, but the confidence measurements remain in the evidence report.'}</small>
              </div>}
              <div className="source-audit">
                <small>{job.result.separation_mode === 'uploaded-horn-stem' ? 'Separation skipped — supplied stem' : `${(job.result.selected_provider || 'wind').toUpperCase()} separated from`}</small>
                <strong>{job.result.separation_mode === 'uploaded-horn-stem' ? job.result.isolated_upload : job.result.source_audio}</strong>
                {windFile && <><span>Wind output: {windFile.download_name || windFile.name}</span><audio controls preload="metadata" src={windFile.url} /></>}
              </div>
              <p className="review-note"><Sparkles size={15} /> {job.result.ai_summary}</p>
              {(simpleScoreFile || advancedScoreFile) && <div className="score-versions">
                <article className="score-version recommended">
                  <div className="version-heading"><span>Recommended</span><h4>Simple</h4></div>
                  <p>Cleaner notation using the detected {job.result.rhythm_grid?.replaceAll('-', ' ') || 'straight'} feel. Usually the more readable starting point.</p>
                  <div className="version-links">
                    {simpleScoreFile && <a href={simpleScoreFile.url} download><FileMusic size={16} /><span><b>MuseScore</b><small>MusicXML</small></span><ArrowDownToLine size={16} /></a>}
                    {simpleMidiFile && <a href={simpleMidiFile.url} download><Music2 size={16} /><span><b>MIDI</b><small>Simple timing</small></span><ArrowDownToLine size={16} /></a>}
                    {simplePdfFile && <a href={simplePdfFile.url} download><FileMusic size={16} /><span><b>PDF</b><small>Rendered locally</small></span><ArrowDownToLine size={16} /></a>}
                  </div>
                </article>
                <article className="score-version">
                  <div className="version-heading"><span>More detail</span><h4>Advanced</h4></div>
                  <p>Preserves the detailed {job.result.rhythm_grid?.replaceAll('-', ' ') || 'straight sixteenth'} grid, dynamics, and short phrase slurs.</p>
                  <div className="version-links">
                    {advancedScoreFile && <a href={advancedScoreFile.url} download><FileMusic size={16} /><span><b>MuseScore</b><small>MusicXML</small></span><ArrowDownToLine size={16} /></a>}
                    {advancedMidiFile && <a href={advancedMidiFile.url} download><Music2 size={16} /><span><b>MIDI</b><small>Advanced timing</small></span><ArrowDownToLine size={16} /></a>}
                    {advancedPdfFile && <a href={advancedPdfFile.url} download><FileMusic size={16} /><span><b>PDF</b><small>Rendered locally</small></span><ArrowDownToLine size={16} /></a>}
                  </div>
                </article>
              </div>}
              <details className="advanced-downloads">
                <summary>Advanced files and diagnostics</summary>
                <div className="downloads">
                {advancedFiles.map((file) => (
                  <a href={file.url} key={file.name} download>
                    <span><FileMusic size={18} /><b>{file.download_name || file.name}</b><small>{(file.size / 1024).toFixed(0)} KB</small></span>
                    <ArrowDownToLine size={18} />
                  </a>
                ))}
                </div>
              </details>
              <p className="draft-warning"><CircleAlert size={15} /> Colored notes are review priorities, not confirmed errors. Confirm them against the original recording by ear.</p>
              <button className="secondary" onClick={resetJob}>Process another passage</button>
            </div>
          )}
        </aside>
      </section>

      <section className="principles">
        <article><b>01</b><h3>Two audio views</h3><p>The original mix supplies beat, harmony and tuning. The isolated stem supplies the sax pitches.</p></article>
        <article><b>02</b><h3>Optional second check</h3><p>For difficult recordings, AI compares the source, horn stem, and generated notes to flag likely transcription mistakes before download.</p></article>
        <article><b>03</b><h3>Editable output</h3><p>MusicXML is the primary deliverable. Open it in MuseScore to finish phrasing and engraving.</p></article>
      </section>

      <footer><span>SaxScribe</span><p>Machine-assisted transcription. Local job files are temporary. OpenAI receives audio only for the optional AI review; configured cloud separators receive audio under their own terms.</p></footer>
    </main>
  )
}

export default App
