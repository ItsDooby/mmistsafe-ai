import React, { useState } from 'react';
import type { Calibration } from './WaterLevelCylinder';

export default function WaterLevelSettings({ deviceId, calibration, onSaved, showSimulatorTools = false }: { deviceId: string; calibration?: Calibration | null; onSaved: () => void; showSimulatorTools?: boolean }) {
  const [message, setMessage] = useState('');
  const [busy, setBusy] = useState(false);
  async function post(route: string, body: object) {
    setBusy(true);
    try {
      const res = await fetch(route, { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ device_id: deviceId, ...body }) });
      const data = await res.json();
      if (!res.ok || data.error || data.success !== true) throw new Error(data.error || 'Unable to save');
      setMessage(data.message || 'Calibration saved');
      onSaved();
    } catch (error) { setMessage(error instanceof Error ? error.message : 'Request failed'); }
    finally { setBusy(false); }
  }
  return <details className="mt-3 text-xs text-ink">
    <summary className="cursor-pointer">Device settings · Water level</summary>
    <form key={JSON.stringify(calibration)} className="flex flex-wrap gap-3 items-end mt-3" onSubmit={event => {
      event.preventDefault();
      const fields = new FormData(event.currentTarget);
      void post('/api/calibration', { empty_distance_cm: Number(fields.get('empty')), full_distance_cm: Number(fields.get('full')) });
    }}>
      <label>Empty distance (cm)<input className="block bg-subtle p-2 rounded w-36" name="empty" type="number" min="0.01" step="any" required defaultValue={calibration?.empty_distance_cm ?? ''} /></label>
      <label>Full distance (cm)<input className="block bg-subtle p-2 rounded w-36" name="full" type="number" min="0" step="any" required defaultValue={calibration?.full_distance_cm ?? ''} /></label>
      <button disabled={busy} className="p-2 rounded bg-subtle">Save calibration</button>
    </form>
    <p className="mt-2">Empty distance must exceed full distance. Percentage measures fill height between these endpoints.</p>
    {showSimulatorTools && deviceId === 'MISTSAFE-SIM-01' && <details className="mt-3"><summary className="cursor-pointer">Simulated level controls</summary>
      <p className="my-2">Test distances use 40 cm empty and 10 cm full. Selection applies on periodic telemetry; paused telemetry stays paused.</p>
      <button disabled={busy} className="p-2 bg-subtle rounded mb-2" onClick={() => void post('/api/calibration', { empty_distance_cm: 40, full_distance_cm: 10 })}>Use test calibration</button>
      <div className="flex flex-wrap gap-2">{['0', '25', '50', '75', '100', 'missing', 'fault'].map(mode => <button disabled={busy} key={mode} className="p-2 bg-subtle rounded" onClick={() => void post('/api/simulator/water-level', { mode })}>{['missing', 'fault'].includes(mode) ? mode : `${mode}%`}</button>)}</div>
    </details>}
    <p role="status" className="mt-2">{message}</p>
  </details>;
}
