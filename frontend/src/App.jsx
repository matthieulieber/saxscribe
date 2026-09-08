import { useEffect, useMemo, useRef, useState } from 'react'
import {
  ArrowDownToLine,
  AudioLines,
  Check,
  CircleAlert,
  CreditCard,
  FileAudio,
  FileMusic,
  Eye,
  LoaderCircle,
  Link2,
  Music2,
  Search,
  SlidersHorizontal,
  Sparkles,
  Square,
  UploadCloud,
  X,
  ZoomIn,
  ZoomOut,
} from 'lucide-react'

const STAGES = [
  { id: 'fetching', label: 'Fetch authorized YouTube audio', icon: Link2 },
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

function YouTubePicker({ value, onChange, authorized, onAuthorizedChange, maxSeconds }) {
  const [dragging, setDragging] = useState(false)
  const dropLink = (event) => {
    event.preventDefault()
    setDragging(false)
    const dropped = event.dataTransfer.getData('text/uri-list') || event.dataTransfer.getData('text/plain')
    if (dropped) onChange(dropped.split(/\r?\n/).find((line) => line && !line.startsWith('#'))?.trim() || '')
  }
  return (
    <div className="youtube-field">
      <div className="field-heading"><label htmlFor="youtube-url">YouTube video</label></div>
      <div
        className={`url-dropzone ${dragging ? 'dragging' : ''} ${value ? 'has-value' : ''}`}
        onDragOver={(event) => { event.preventDefault(); setDragging(true) }}
        onDragLeave={() => setDragging(false)}
        onDrop={dropLink}
      >
        <span className="drop-icon"><Link2 size={22} /></span>
        <input
          id="youtube-url"
          type="url"
          inputMode="url"
          value={value}
          onChange={(event) => onChange(event.target.value)}
          placeholder="Paste or drop a youtube.com link"
          autoComplete="off"
        />
        {value && <span className="file-check"><Check size={16} /></span>}
      </div>
      <small className="source-limit">One public video, up to {Math.floor((maxSeconds || 480) / 60)} minutes. Playlists, live streams, private videos and age-restricted videos are not supported.</small>
      <label className="rights-check">
        <input type="checkbox" checked={authorized} onChange={(event) => onAuthorizedChange(event.target.checked)} />
        <span>I am authorized under YouTube's terms and applicable law to download and process this video.</span>
      </label>
    </div>
  )
}

function ScoreViewer({ simpleFile, advancedFile }) {
  const [open, setOpen] = useState(false)
  const [version, setVersion] = useState('simple')
  const [zoom, setZoom] = useState(0.85)
  const [loading, setLoading] = useState(false)
  const [viewerError, setViewerError] = useState('')
  const containerRef = useRef(null)
  const rendererRef = useRef(null)
  const selectedFile = version === 'advanced' ? advancedFile : simpleFile

  useEffect(() => {
    if (!open) return undefined
    const closeOnEscape = (event) => event.key === 'Escape' && setOpen(false)
    document.addEventListener('keydown', closeOnEscape)
    const previousOverflow = document.body.style.overflow
    document.body.style.overflow = 'hidden'
    return () => {
      document.removeEventListener('keydown', closeOnEscape)
      document.body.style.overflow = previousOverflow
    }
  }, [open])

  useEffect(() => {
    if (!open || !selectedFile?.url || !containerRef.current) return undefined
    let cancelled = false
    let renderer = null
    setLoading(true)
    setViewerError('')
    containerRef.current.replaceChildren()
    Promise.all([
      fetch(selectedFile.url).then(async (response) => {
        if (!response.ok) throw new Error('The MusicXML file could not be loaded.')
        return response.text()
      }),
      import('opensheetmusicdisplay'),
    ])
      .then(([musicXml, module]) => {
        if (cancelled) return undefined
        renderer = new module.OpenSheetMusicDisplay(containerRef.current, {
          autoResize: true,
          backend: 'svg',
        })
        rendererRef.current = renderer
        renderer.Zoom = zoom
        return renderer.load(musicXml)
      })
      .then(() => {
        if (!cancelled && renderer) renderer.render()
      })
      .catch((reason) => {
        if (!cancelled) setViewerError(reason.message || 'The score could not be displayed.')
      })
      .finally(() => {
        if (!cancelled) setLoading(false)
      })
    return () => {
      cancelled = true
      if (renderer && typeof renderer.clear === 'function') renderer.clear()
      rendererRef.current = null
    }
  }, [open, selectedFile?.url, version])

  useEffect(() => {
    if (!rendererRef.current || loading) return
    rendererRef.current.Zoom = zoom
    rendererRef.current.render()
  }, [zoom, loading])

  if (!simpleFile && !advancedFile) return null
  const changeVersion = (nextVersion) => {
    if ((nextVersion === 'simple' && simpleFile) || (nextVersion === 'advanced' && advancedFile)) {
      setVersion(nextVersion)
    }
  }
  const changeZoom = (amount) => setZoom((current) => Math.min(1.4, Math.max(0.55, Number((current + amount).toFixed(2)))))

  return <>
    <button type="button" className="score-preview-button" onClick={() => { setVersion(simpleFile ? 'simple' : 'advanced'); setOpen(true) }}><Eye size={17} /> View generated sheet music</button>
    {open && <div className="score-modal-backdrop" role="presentation" onMouseDown={(event) => event.target === event.currentTarget && setOpen(false)}>
      <section className="score-modal" role="dialog" aria-modal="true" aria-label="Generated sheet music">
        <header className="score-modal-header">
          <div><small>Generated notation</small><h3>{version === 'simple' ? 'Playable lead sheet' : 'Detailed performance'}</h3></div>
          <button type="button" aria-label="Close score viewer" onClick={() => setOpen(false)}><X size={20} /></button>
        </header>
        <div className="score-toolbar">
          <div className="score-tabs" role="group" aria-label="Score version">
            <button type="button" className={version === 'simple' ? 'selected' : ''} disabled={!simpleFile} onClick={() => changeVersion('simple')}>Lead Sheet</button>
            <button type="button" className={version === 'advanced' ? 'selected' : ''} disabled={!advancedFile} onClick={() => changeVersion('advanced')}>Performance</button>
          </div>
          <div className="zoom-controls" role="group" aria-label="Score zoom">
            <button type="button" aria-label="Zoom out" disabled={zoom <= 0.55} onClick={() => changeZoom(-0.1)}><ZoomOut size={16} /></button>
            <span>{Math.round(zoom * 100)}%</span>
            <button type="button" aria-label="Zoom in" disabled={zoom >= 1.4} onClick={() => changeZoom(0.1)}><ZoomIn size={16} /></button>
          </div>
          {selectedFile && <a href={selectedFile.url} download><ArrowDownToLine size={15} /> Download MusicXML</a>}
        </div>
        <div className="score-canvas-wrap">
          {loading && <p className="score-loading"><LoaderCircle className="spin" size={18} /> Engraving the score…</p>}
          {viewerError && <p className="error"><CircleAlert size={16} /> {viewerError} Download the MusicXML and open it in MuseScore instead.</p>}
          <div className="score-canvas" ref={containerRef} />
        </div>
        <div className="score-modal-footer">Browser preview is for review. MusicXML remains the editable source of truth.</div>
      </section>
    </div>}
  </>
}

function ReviewAudio({ file }) {
  const audioRef = useRef(null)
  const [speed, setSpeed] = useState(1)

  useEffect(() => {
    setSpeed(1)
    if (audioRef.current) audioRef.current.playbackRate = 1
  }, [file?.url])

  const changeSpeed = (nextSpeed) => {
    setSpeed(nextSpeed)
    if (audioRef.current) audioRef.current.playbackRate = nextSpeed
  }

  if (!file) return null
  return <div className="review-audio">
    <audio ref={audioRef} controls preload="metadata" src={file.url} />
    <div className="playback-speed" role="group" aria-label="Review playback speed">
      <span>Playback speed</span>
      {[0.75, 1, 1.25].map((value) => (
        <button type="button" className={speed === value ? 'selected' : ''} onClick={() => changeSpeed(value)} key={value}>{value}×</button>
      ))}
    </div>
  </div>
}

function App() {
  const [health, setHealth] = useState(null)
  const [billingConfig, setBillingConfig] = useState(null)
  const [original, setOriginal] = useState(null)
  const [isolated, setIsolated] = useState(null)
  const [inputMode, setInputMode] = useState('upload')
  const [youtubeUrl, setYoutubeUrl] = useState('')
  const [youtubeAuthorized, setYoutubeAuthorized] = useState(false)
  const [title, setTitle] = useState('')
  const [artist, setArtist] = useState('')
  const [instrument, setInstrument] = useState('tenor')
  const [plan, setPlan] = useState('free')
  const [checkoutSessionId, setCheckoutSessionId] = useState('')
  const [checkoutBusy, setCheckoutBusy] = useState(false)
  const [highlightUncertain, setHighlightUncertain] = useState(true)
  const [job, setJob] = useState(null)
  const [error, setError] = useState('')
  const hosted = health?.runtime_mode === 'gcp'
  const useAi = hosted && plan === 'enhanced'

  useEffect(() => {
    fetch('/api/health').then((response) => response.json()).then(setHealth).catch(() => setHealth({ ok: false }))
    fetch('/api/billing/config').then((response) => response.json()).then(setBillingConfig).catch(() => setBillingConfig(null))
    const params = new URLSearchParams(window.location.search)
    if (params.get('checkout') === 'cancelled') {
      setPlan('enhanced')
      setCheckoutSessionId('')
      window.localStorage.removeItem('saxscribe-enhanced-session')
      setError('Payment was cancelled. No charge was used and no source was submitted.')
      window.history.replaceState({}, '', window.location.pathname)
    }
    const returnedSession = params.get('checkout') === 'success' ? params.get('session_id') : ''
    const pendingSession = returnedSession || (params.get('checkout') === 'cancelled' ? '' : window.localStorage.getItem('saxscribe-enhanced-session') || '')
    const savedJobId = window.localStorage.getItem('saxscribe-active-job')
    if (pendingSession && savedJobId && !returnedSession) {
      setCheckoutSessionId(pendingSession)
      setPlan('enhanced')
    } else if (pendingSession) {
      setCheckoutBusy(true)
      fetch(`/api/billing/session/${encodeURIComponent(pendingSession)}`)
        .then(async (response) => {
          const payload = await response.json()
          if (!response.ok) throw new Error(payload.detail || 'Payment could not be verified.')
          setCheckoutSessionId(pendingSession)
          window.localStorage.setItem('saxscribe-enhanced-session', pendingSession)
          setPlan('enhanced')
          setError('')
        })
        .catch((reason) => {
          window.localStorage.removeItem('saxscribe-enhanced-session')
          setError(reason.message)
        })
        .finally(() => {
          setCheckoutBusy(false)
          if (returnedSession) window.history.replaceState({}, '', window.location.pathname)
        })
    }
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

  const stages = useMemo(() => STAGES.filter((stage) => {
    if (stage.id === 'reviewing' && !(job?.use_ai ?? useAi)) return false
    if (stage.id === 'fetching' && (job?.source_type ?? inputMode) !== 'youtube') return false
    return true
  }), [job?.source_type, job?.use_ai, inputMode, useAi])

  const currentIndex = useMemo(() => {
    if (!job) return -1
    const aliases = { complete: stages.length }
    return aliases[job.stage] ?? stages.findIndex((stage) => stage.id === job.stage)
  }, [job, stages])

  async function submit(event) {
    event.preventDefault()
    setError('')
    if (inputMode === 'upload' && !original) {
      setError('Add the original recording first. It supplies tempo and harmony evidence; downbeat placement still needs review.')
      return
    }
    if (inputMode === 'youtube' && !youtubeUrl.trim()) {
      setError('Paste or drop one YouTube video link first.')
      return
    }
    if (inputMode === 'youtube' && !youtubeAuthorized) {
      setError("Confirm that you are authorized under YouTube's terms and applicable law to process this video.")
      return
    }
    if (hosted && plan === 'enhanced' && !checkoutSessionId) {
      setError('Complete the one-time Enhanced payment before submitting a source.')
      return
    }
    const data = new FormData()
    if (inputMode === 'upload' && original) data.append('original', original)
    if (inputMode === 'youtube') {
      data.append('youtube_url', youtubeUrl.trim())
      data.append('youtube_authorized', String(youtubeAuthorized))
    }
    if (isolated) data.append('isolated', isolated)
    data.append('title', title)
    data.append('artist', artist)
    data.append('instrument', instrument)
    data.append('plan', hosted ? plan : 'free')
    data.append('checkout_session_id', hosted ? checkoutSessionId : '')
    data.append('highlight_uncertain', String(highlightUncertain))
    setJob({ status: 'uploading', stage: 'queued', percent: 2, message: inputMode === 'youtube' ? 'Submitting the YouTube link' : 'Uploading the recording' })
    try {
      const response = await fetch('/api/jobs', { method: 'POST', body: data })
      const payload = await response.json()
      if (!response.ok) {
        if (response.status === 409 && plan === 'enhanced') {
          window.localStorage.removeItem('saxscribe-enhanced-session')
          setCheckoutSessionId('')
        }
        throw new Error(payload.detail || 'The job could not start.')
      }
      setJob({ ...payload, status: 'queued', stage: 'queued', percent: 3, message: 'Waiting for the transcription worker' })
    } catch (reason) {
      setError(reason.message)
      setJob(null)
    }
  }

  async function beginCheckout() {
    setError('')
    setCheckoutBusy(true)
    try {
      const response = await fetch('/api/billing/checkout', { method: 'POST' })
      const payload = await response.json()
      if (!response.ok) throw new Error(payload.detail || 'Secure checkout could not start.')
      window.location.assign(payload.url)
    } catch (reason) {
      setError(reason.message)
      setCheckoutBusy(false)
    }
  }

  function choosePlan(nextPlan) {
    setError('')
    setPlan(nextPlan)
    if (nextPlan === 'enhanced' && !checkoutSessionId) {
      setOriginal(null)
      setIsolated(null)
      setYoutubeUrl('')
      setYoutubeAuthorized(false)
    }
  }

  function chooseInputMode(nextMode) {
    setError('')
    setInputMode(nextMode)
    if (nextMode === 'youtube') {
      setOriginal(null)
      setIsolated(null)
    } else {
      setYoutubeUrl('')
      setYoutubeAuthorized(false)
    }
  }

  function resetJob() {
    window.localStorage.removeItem('saxscribe-active-job')
    setJob(null)
  }

  useEffect(() => {
    if (job?.plan === 'enhanced' && job?.status === 'complete') {
      window.localStorage.removeItem('saxscribe-enhanced-session')
      setCheckoutSessionId('')
    }
  }, [job?.plan, job?.status])

  const busy = job && !['complete', 'error', 'cancelled'].includes(job.status)
  const selectedPlanReady = !hosted
    ? health?.free_ready
    : plan === 'enhanced'
      ? health?.enhanced_ready && Boolean(checkoutSessionId)
      : health?.free_ready
  const facts = job?.result?.facts
  const windFile = job?.result?.files?.find((file) => file.role === 'wind_stem')
  const simpleScoreFile = job?.result?.files?.find((file) => file.role === 'musicxml')
  const simpleMidiFile = job?.result?.files?.find((file) => file.role === 'simple_midi')
  const advancedScoreFile = job?.result?.files?.find((file) => file.role === 'advanced_musicxml')
  const advancedMidiFile = job?.result?.files?.find((file) => file.role === 'advanced_midi')
  const simplePdfFile = job?.result?.files?.find((file) => file.role === 'simple_pdf')
  const advancedPdfFile = job?.result?.files?.find((file) => file.role === 'advanced_pdf')
  const simpleConfidence = job?.result?.note_confidence?.simple
  const scoreRefinement = job?.result?.score_refinement
  return (
    <main>
      <header className="topbar">
        <a className="brand" href="#top" aria-label="SaxScribe home">
          <span className="brand-mark"><Music2 size={19} /></span>
          <span>SaxScribe</span>
          <em>{hosted ? 'BETA' : 'LOCAL'}</em>
        </a>
        <div className={`server-pill ${health?.ok ? 'online' : ''}`}>
          <span /> {health === null ? 'Checking engine' : !health.ok ? 'Engine offline' : `${health.runtime_mode === 'gcp' ? 'Hosted' : 'Local'} engine · build ${health.build}`}
        </div>
      </header>

      <section className="hero" id="top">
        <p className="eyebrow">Audio → editable notation</p>
        <h1>Turn a sax line into<br /><i>sheet music you can use.</i></h1>
        <p className="lede">Upload audio or drop a YouTube link. Use the included transcription workflow, or pay once for enhanced isolation bundled with a required AI evidence review.</p>
        <div className="pipeline-line" aria-label="Processing stages">
          <span>Full mix</span><b>01</b><span>Horn stem</span><b>02</b><span>Clean MIDI</span><b>03</b><span>Score</span>
        </div>
      </section>

      <section className="workspace">
        <form className="panel form-panel" onSubmit={submit}>
          <div className="panel-title">
            <span>01</span>
            <div><h2>Source material</h2><p>Upload the full mix or paste a permitted YouTube link. The recording anchors rhythm and key.</p></div>
          </div>

          {hosted ? (
            <fieldset className="plan-picker">
              <legend>Choose a transcription</legend>
              <div className="plan-grid">
                <button type="button" className={`plan-card ${plan === 'free' ? 'selected' : ''}`} onClick={() => choosePlan('free')}>
                  <span className="plan-heading"><b>Free</b><strong>Free</strong></span>
                  <small>Included horn isolation</small>
                  <small>Deterministic cleanup and confidence colors</small>
                  <em>No AI review</em>
                </button>
                <button
                  type="button"
                  className={`plan-card enhanced ${plan === 'enhanced' ? 'selected' : ''}`}
                  onClick={() => choosePlan('enhanced')}
                  disabled={!billingConfig?.enhanced?.available && !checkoutSessionId}
                >
                  <span className="plan-heading"><b>Enhanced</b><strong>{checkoutSessionId ? 'Paid' : (billingConfig?.enhanced?.price || 'Loading…')}</strong></span>
                  <small>Enhanced horn isolation</small>
                  <small>Required audio and score AI review</small>
                  <small>Measured second-pass score refinement</small>
                  <em>One transcription · one payment</em>
                </button>
              </div>
              {billingConfig?.enhanced?.reason && !billingConfig.enhanced.available && <p className="warning"><CircleAlert size={15} /> {billingConfig.enhanced.reason}</p>}
            </fieldset>
          ) : (
            <p className="local-plan"><Check size={15} /> Local mode uses the included offline workflow. No payment or external AI review is used.</p>
          )}

          {hosted && plan === 'enhanced' && !checkoutSessionId ? (
            <div className="payment-gate">
              <span><CreditCard size={22} /></span>
              <h3>Unlock one Enhanced transcription</h3>
              <p>Payment covers enhanced horn isolation and a bounded two-pass comparison of the recording, rendered draft, MIDI, and MusicXML.</p>
              <button type="button" className="primary" disabled={checkoutBusy || !billingConfig?.enhanced?.available} onClick={beginCheckout}>
                {checkoutBusy ? <><LoaderCircle className="spin" size={18} /> Opening secure checkout…</> : <><CreditCard size={18} /> Pay {billingConfig?.enhanced?.price || ''} with Stripe</>}
              </button>
              <small>Choose an upload or YouTube source after payment returns you here. Enhanced sends the recording to LALAL.AI and review material to OpenAI. Card details go directly to Stripe.</small>
              {error && <p className="error"><CircleAlert size={17} />{error}</p>}
            </div>
          ) : <>

          <div className="source-method" role="group" aria-label="Choose audio source">
            <button type="button" className={inputMode === 'upload' ? 'selected' : ''} onClick={() => chooseInputMode('upload')}><UploadCloud size={15} /> Upload audio</button>
            <button type="button" className={inputMode === 'youtube' ? 'selected' : ''} disabled={health?.youtube_input_enabled === false} onClick={() => chooseInputMode('youtube')}><Link2 size={16} /> YouTube link</button>
          </div>
          {inputMode === 'upload' ? (
            <FilePicker id="original" label="Original recording" hint="WAV, M4A, MP3, AIFF or FLAC" file={original} onChange={setOriginal} />
          ) : (
            <YouTubePicker value={youtubeUrl} onChange={setYoutubeUrl} authorized={youtubeAuthorized} onAuthorizedChange={setYoutubeAuthorized} maxSeconds={health?.max_youtube_seconds} />
          )}
          {plan === 'free' && inputMode === 'upload' && <FilePicker id="isolated" label="Already isolated horn" hint="Skip automatic isolation and avoid processing twice" file={isolated} onChange={setIsolated} optional />}
          {isolated && <p className="warning"><CircleAlert size={15} /> Automatic isolation will be skipped because an isolated-horn file is selected.</p>}
          {!isolated && plan === 'free' && (
            <p className="metadata-note"><SlidersHorizontal size={14} /> Included transcription workflow · {health?.free_ready ? 'ready' : 'not configured'}</p>
          )}
          {!isolated && plan === 'enhanced' && (
            <p className="metadata-note"><SlidersHorizontal size={14} /> Enhanced workflow · {health?.enhanced_ready ? 'ready' : 'not configured'} · payment {checkoutSessionId ? 'verified' : 'required'}</p>
          )}
          {!isolated && selectedPlanReady === false && <p className="error"><CircleAlert size={15} /> The selected workflow is not configured on this server.</p>}

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

          {plan === 'enhanced' && <p className="enhanced-review"><Sparkles size={16} /><span><strong>AI review included</strong><small>Enhanced compares the recording with a rendered draft, then checks the lead sheet again. Changes still require measured pitch evidence. Revisions are kept only when quality improves without adding clutter.</small></span></p>}

          {error && <p className="error"><CircleAlert size={17} />{error}</p>}
          <button className="primary" disabled={busy || health?.ok === false || (!isolated && selectedPlanReady === false)}>
            {busy ? <><LoaderCircle className="spin" size={18} /> Processing…</> : <><Sparkles size={18} /> Create {hosted && plan === 'enhanced' ? 'Enhanced' : 'free'} sax score</>}
          </button>
          </>}
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
              <p className="patient-note">Processing input: <b>{job.source_name || original?.name || 'YouTube link'}</b>{job.isolated_source_name ? ` · using supplied horn audio ${job.isolated_source_name}` : ''}</p>
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
              <div className="success-title"><span><Check size={20} /></span><div><h3>Two interpretations ready</h3><p>{job.result.simple_notes ?? job.result.notes} lead-sheet notes · {job.result.advanced_notes ?? job.result.notes} performed notes</p></div></div>
              <p className={`result-plan ${job.plan === 'enhanced' ? 'enhanced' : ''}`}><Sparkles size={14} /> {job.plan === 'enhanced' ? 'Enhanced · premium processing + AI review' : 'Free · standard processing'}</p>
              <div className="facts-grid">
                <div><small>Concert key</small><strong>{facts.concert_key} {facts.mode}</strong></div>
                <div><small>Tempo</small><strong>{Math.round(facts.bpm)} BPM</strong></div>
                <div><small>Meter</small><strong>{facts.meter}</strong></div>
                <div><small>Part</small><strong>{(job.instrument || instrument) === 'concert' ? 'Concert' : (job.instrument || instrument)[0].toUpperCase() + (job.instrument || instrument).slice(1)}</strong></div>
                <div><small>Stem quality</small><strong>{job.result.confidence?.level || 'Review'} </strong></div>
                <div><small>Rhythm</small><strong>{job.result.rhythm_grid?.replaceAll('-', ' ') || 'Review'}</strong></div>
              </div>
              {simpleConfidence && <div className="note-confidence">
                <div className="confidence-heading"><strong>Lead-sheet confidence</strong><small>{simpleConfidence.uncertain} notes to review</small></div>
                <div className="confidence-counts">
                  <span><i className="confidence-dot high" />{simpleConfidence.high} high</span>
                  <span><i className="confidence-dot medium" />{simpleConfidence.medium} review</span>
                  <span><i className="confidence-dot low" />{simpleConfidence.low} weak</span>
                </div>
                {simpleConfidence.total > 0 && simpleConfidence.low / simpleConfidence.total >= 0.6 && (
                  <p className="confidence-failure"><CircleAlert size={13} /> Most notes lack support from the horn stem. Do not trust this transcription yet; check that the correct horn-only file was used.</p>
                )}
                {simpleConfidence.possible_non_horn > 0 && <p><CircleAlert size={13} /> {simpleConfidence.possible_non_horn} {simpleConfidence.possible_non_horn === 1 ? 'note has' : 'notes have'} possible leakage or non-horn evidence. This does not identify vocals conclusively.</p>}
                <small>{job.result.highlight_uncertain_notes ? 'The same orange/red flags are embedded in both MusicXML scores.' : 'Score coloring was disabled, but the confidence measurements remain in this result summary.'}</small>
              </div>}
              <div className="source-audit">
                <small>{job.result.isolated_upload ? 'Supplied horn recording' : 'Horn audio created from'}</small>
                <strong>{job.result.isolated_upload || job.result.source_audio}</strong>
                {windFile && <><span>Isolated horn audio</span><ReviewAudio file={windFile} /></>}
              </div>
              <p className="review-note"><Sparkles size={15} /> {job.result.ai_summary}</p>
              {job.plan === 'enhanced' && scoreRefinement && (
                <div className="refinement-result">
                  <strong>{scoreRefinement.passes_run === 2 ? 'Two-pass score check complete' : 'Score check stopped early'}</strong>
                  <small>{scoreRefinement.accepted_revisions
                    ? `A revision was retained after its measured score improved from ${scoreRefinement.initial_score.toFixed(1)} to ${scoreRefinement.final_score.toFixed(1)}.`
                    : 'No second-pass revision was retained because it did not clear the measured quality and readability gates.'}</small>
                </div>
              )}
              <ScoreViewer simpleFile={simpleScoreFile} advancedFile={advancedScoreFile} />
              {(simpleScoreFile || advancedScoreFile) && <div className="score-versions">
                <article className="score-version recommended">
                  <div className="version-heading"><span>Recommended</span><h4>Playable Lead Sheet</h4></div>
                  <p>Normalizes rhythmic spelling on the coarsest standard grid that keeps distinct supported notes. Only short weak artifacts are removed; uncertain phrases remain visible for review.</p>
                  <div className="version-links">
                    {simpleScoreFile && <a href={simpleScoreFile.url} download><FileMusic size={16} /><span><b>MuseScore</b><small>MusicXML</small></span><ArrowDownToLine size={16} /></a>}
                    {simpleMidiFile && <a href={simpleMidiFile.url} download><Music2 size={16} /><span><b>MIDI</b><small>Lead-sheet timing</small></span><ArrowDownToLine size={16} /></a>}
                    {simplePdfFile && <a href={simplePdfFile.url} download><FileMusic size={16} /><span><b>PDF</b><small>Rendered locally</small></span><ArrowDownToLine size={16} /></a>}
                  </div>
                </article>
                <article className="score-version">
                  <div className="version-heading"><span>Literal version</span><h4>Detailed Performance</h4></div>
                  <p>Preserves the detected {job.result.rhythm_grid?.replaceAll('-', ' ') || 'straight sixteenth'} timing, including performance irregularities, dynamics, and short phrase slurs.</p>
                  <div className="version-links">
                    {advancedScoreFile && <a href={advancedScoreFile.url} download><FileMusic size={16} /><span><b>MuseScore</b><small>MusicXML</small></span><ArrowDownToLine size={16} /></a>}
                    {advancedMidiFile && <a href={advancedMidiFile.url} download><Music2 size={16} /><span><b>MIDI</b><small>Performed timing</small></span><ArrowDownToLine size={16} /></a>}
                    {advancedPdfFile && <a href={advancedPdfFile.url} download><FileMusic size={16} /><span><b>PDF</b><small>Rendered locally</small></span><ArrowDownToLine size={16} /></a>}
                  </div>
                </article>
              </div>}
              <p className="draft-warning"><CircleAlert size={15} /> Colored notes are review priorities, not confirmed errors. Confirm them against the original recording by ear.</p>
              <button className="secondary" onClick={resetJob}>Process another passage</button>
            </div>
          )}
        </aside>
      </section>

      <section className="principles">
        <article><b>01</b><h3>Two audio views</h3><p>The original mix supplies beat, harmony and tuning. The isolated stem supplies the sax pitches.</p></article>
        <article><b>02</b><h3>Pay only for enhancement</h3><p>The included workflow is free. Enhanced bundles premium horn isolation with the complete AI evidence review for one transcription.</p></article>
        <article><b>03</b><h3>Editable output</h3><p>MusicXML is the primary deliverable. Open it in MuseScore to finish phrasing and engraving.</p></article>
      </section>

      <footer><span>SaxScribe</span><p>Machine-assisted transcription. YouTube links require permission to download and process. Enhanced uses external processing services; the named processor disclosure appears before payment. Free does not send audio to those services. Payment card details are handled by Stripe.</p></footer>
    </main>
  )
}

export default App
