import { useEffect, useState } from 'react'

async function request(url, options) {
  const response = await fetch(url, options)
  const payload = await response.json()
  if (!response.ok) throw new Error(payload.detail || 'Reference request failed.')
  return payload
}

export default function ReferenceLibrary({ instrument, job, onLoad }) {
  const [items, setItems] = useState([])
  const [selected, setSelected] = useState('')
  const [error, setError] = useState('')
  const [busy, setBusy] = useState(false)
  const [report, setReport] = useState(null)
  const item = items.find(value => value.id === selected)
  async function refresh() {
    setItems(await request('/api/references'))
  }
  useEffect(() => { refresh().catch(err => setError(err.message)) }, [])
  useEffect(() => { setReport(null) }, [selected, job?.id])
  async function run(action) {
    setBusy(true); setError('')
    try { await action() } catch (err) { setError(err.message) }
    finally { setBusy(false) }
  }
  async function create(event) {
    event.preventDefault()
    const form = event.currentTarget
    const data = new FormData(form)
    data.append('instrument', instrument)
    await run(async () => {
      const saved = await request('/api/references', { method: 'POST', body: data })
      await refresh(); setSelected(saved.id); form.reset()
    })
  }
  async function attach(event) {
    event.preventDefault()
    const data = new FormData(event.currentTarget)
    await run(async () => {
      await request(`/api/references/${selected}/notes`, { method: 'POST', body: data })
      setReport(null); await refresh()
    })
  }
  async function loadAudio() {
    await run(async () => {
      const response = await fetch(`/api/references/${selected}/files/${item.audio}`)
      if (!response.ok) throw new Error('Could not load the reference recording.')
      onLoad(new File([await response.blob()], item.audio), item)
    })
  }
  return <section className="panel reference-library">
    <h2>Reference recordings</h2>
    <p>Keep a recording with its matching score to check transcription changes. This measures pitch sequences; it does not train the model or evaluate rhythm.</p>
    <details><summary>Add a recording and matching PDF</summary>
      <form onSubmit={create} className="field-grid">
        <label>Title<input name="title" required maxLength={200} placeholder="Minor Blues Etude" /></label>
        <label>Recording<input name="audio" type="file" required accept="audio/*,.aif,.aiff" /></label>
        <label>Matching score<input name="score" type="file" required accept="application/pdf" /></label>
        <p>Score instrument: {instrument}</p>
        <button className="secondary" disabled={busy}>Save reference</button>
      </form>
    </details>
    <label>Saved reference<select value={selected} onChange={event => setSelected(event.target.value)}>
      <option value="">Choose a reference</option>
      {items.map(value => <option key={value.id} value={value.id}>{value.title} · {value.instrument}</option>)}
    </select></label>
    {item && <div>
      <p>{item.status === 'ready' ? 'Verified reference notes ready' : 'PDF saved — verified reference notes still needed'}</p>
      <audio key={selected} controls preload="none" src={`/api/references/${selected}/files/${item.audio}`} />
      <p><a href={`/api/references/${selected}/files/score.pdf`} target="_blank" rel="noreferrer">Open matching PDF</a></p>
      <button type="button" className="secondary" disabled={busy || ['queued', 'running', 'uploading'].includes(job?.status)} onClick={loadAudio}>Use this recording for transcription</button>
      <details><summary>Add or replace verified melody MIDI</summary>
        <form onSubmit={attach} className="field-grid">
          <label>Melody only<input name="midi" type="file" required accept=".mid,.midi" /></label>
          <label>MIDI pitch basis<select name="pitch_basis" defaultValue="concert"><option value="concert">Concert / sounding pitch (usual MIDI export)</option><option value="written">Written score pitch</option></select></label>
          <label><input type="checkbox" name="verified" value="true" required /> I checked these notes against the matching PDF.</label>
          <button className="secondary" disabled={busy}>Save verified notes</button>
        </form>
      </details>
      <p>Compare only a completed transcription of this same recording. Counts reflect the MIDI attacks; score ties should be exported as one sustained note.</p>
      <button type="button" className="secondary" disabled={busy || item.status !== 'ready' || job?.status !== 'complete'} onClick={() => run(async () => setReport(await request(`/api/references/${selected}/compare/${job.id}`, { method: 'POST' })))}>Compare current transcription</button>
      {report && <div aria-live="polite"><table><caption>Pitch sequence comparison</caption><thead><tr><th>Version</th><th>Precision</th><th>Recall</th><th>Wrong pitches</th><th>Missing</th><th>Extra</th></tr></thead><tbody>
        {Object.entries(report.reports).map(([name, value]) => <tr key={name}><td>{name === 'simple_midi' ? 'Playable lead sheet' : 'Detailed performance'}</td><td>{(100 * value.pitch_precision).toFixed(1)}%</td><td>{(100 * value.pitch_recall).toFixed(1)}%</td><td>{value.substitutions}</td><td>{value.missing}</td><td>{value.extra}</td></tr>)}
      </tbody></table><p>Precision: fraction of generated notes matched. Recall: fraction of reference notes matched. Timing, rhythm, rests and engraving are not scored.</p>
      <details><summary>Note differences</summary><pre>{JSON.stringify(report.reports, null, 2)}</pre></details></div>}
    </div>}
    {error && <p role="alert" className="error">{error}</p>}
  </section>
}
