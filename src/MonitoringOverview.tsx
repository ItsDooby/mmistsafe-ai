import React from 'react';
import { ArrowUpRight, AlertCircle, ChevronRight, CircleCheck, Clock3 } from 'lucide-react';
import type { SystemStatus } from './App';
import WaterLevelCylinder from './WaterLevelCylinder';
import WaterLevelSettings from './WaterLevelSettings';

const measurements = [
  { key: 'temp_C', sensor: 'temp', label: 'Temperature', unit: '°C', digits: 1 },
  { key: 'pH', sensor: 'pH', label: 'pH', unit: 'pH', digits: 2 },
  { key: 'EC_uS_cm', sensor: 'EC', label: 'Conductivity', unit: 'µS/cm', digits: 1 },
  { key: 'turbidity_NTU', sensor: 'turbidity', label: 'Turbidity', unit: 'NTU', digits: 2 },
  { key: 'flow_L_min', sensor: 'flow', label: 'Flow rate', unit: 'L/min', digits: 2 },
  { key: 'stagnation_hours', sensor: 'stagnation', label: 'Time without flow', unit: 'h', digits: 1 },
] as const;

const faultName = (fault: string) => ({ E01_FLOW_LOW: 'Flow loss', E02_UV_FAIL: 'UV fault', E02_UV_FAULT: 'UV fault', E03_SENSOR_FAULT: 'Sensor fault' }[fault] || fault.replace(/_/g, ' '));
function activityLabel(event: any, simulated: boolean) {
  if (event.event_type === 'COMMAND_ACK') return `${simulated ? 'Simulator' : 'Device'} acknowledged a command`;
  if (event.event_type === 'COMMAND_QUEUED') return 'Equipment command queued';
  if (event.event_type === 'FAULT') return event.message.replace('Reported hardware fault code:', 'Device fault:');
  return event.message.replace(/cmd-[a-z0-9-]+/gi, '(details in Activity)').replace('Hardware ACK', simulated ? 'Simulator ACK' : 'Device ACK');
}
function operatingState(status: SystemStatus | null, failed: boolean) {
  const state = status?.state;
  const safety = status?.safety;
  const fresh = Boolean(status?.is_fresh);
  const faults = state?.fault_codes || [];
  if (safety?.manual_stop_latched) {
    const reportsStopped = fresh && state?.pump_state === 'OFF' && state?.valve_state === 'CLOSED'
      && safety.command_status === 'DEVICE REPORTS STOPPED';
    return { title: reportsStopped ? 'Stopped — device reported' : 'Shutdown requested — awaiting confirmation', tone: 'warning', note: reportsStopped ? 'The stop latch remains active. Reported states do not establish physical verification.' : 'The platform stop latch is active. Device acknowledgement and equipment feedback are tracked separately.' };
  }
  if (!state) return { title: failed ? 'Device status unavailable' : 'Waiting for device', tone: 'neutral', note: failed ? 'The platform could not retrieve status. Check the connection and try again.' : 'Readings will appear when this device sends its first update.' };
  if (!fresh) return { title: 'Connection lost — last feedback stale', tone: 'warning', note: 'Last readings are retained below. Current equipment state is unconfirmed.' };
  if (state.local_interlock_state === 'TRIPPED' || faults.length || state.uv_status === 'FAULT') return { title: `${state.pump_state === 'OFF' && state.valve_state === 'CLOSED' ? 'System stopped' : 'Device fault reported'} · ${faults.length ? faultName(faults[0]) : state.uv_status === 'FAULT' ? 'UV fault' : 'device interlock'}`, tone: 'danger', note: 'The device reports an active fault or interlock. Resolve the cause before reset; reset does not start equipment.' };
  if (state.pump_state === 'ON' && state.valve_state === 'OPEN') return { title: 'Running — device reported', tone: 'normal', note: 'The device reports the pump on and valve open. Water safety is not assessed by this status.' };
  if (state.pump_state === 'OFF' && state.valve_state === 'CLOSED') return { title: 'Stopped — device reported', tone: 'neutral', note: 'The device reports the pump off and valve closed. Starting is a separate action.' };
  return { title: 'Equipment feedback incomplete', tone: 'neutral', note: 'Fresh readings received; equipment feedback does not establish a running or stopped state.' };
}

interface Props {
  deviceId: string;
  status: SystemStatus | null;
  statusError: string | null;
  events: any[];
  history: any[];
  busy: boolean;
  onReset: () => void;
  onStart: () => void;
  onRefresh: () => void;
  onNavigate: (tab: 'trends' | 'controls' | 'verification') => void;
}

export default function MonitoringOverview({ deviceId, status, statusError, events, history, busy, onReset, onStart, onRefresh, onNavigate }: Props) {
  const state = status?.state;
  const summary = operatingState(status, Boolean(statusError));
  const AttentionIcon = state && status?.is_fresh ? CircleCheck : Clock3;
  const simulated = deviceId === 'MISTSAFE-SIM-01';
  const issues: { title: string; next: string }[] = [];
  if (statusError) issues.push({ title: 'Status could not be refreshed', next: 'Check the platform connection. Displayed readings may be old.' });
  if (state && !status?.is_fresh) issues.push({ title: 'Device feedback is stale', next: simulated ? 'Check whether simulated readings are paused in Developer tools.' : 'Check device power and its connection. Current equipment state is unknown.' });
  if (status?.safety.manual_stop_latched) issues.push({ title: 'Manual stop latch active', next: simulated ? 'Resolve faults, then reset the test device. Start remains a separate action.' : 'Resolve faults and reset locally on the ESP32. Await fresh confirmation.' });
  for (const fault of state?.fault_codes || []) issues.push({ title: faultName(fault), next: simulated ? 'Clear the simulated fault in Developer tools, then reset when eligible.' : 'Inspect the affected equipment locally and resolve the fault before resetting.' });
  if (state?.uv_status === 'FAULT' && !state.fault_codes.some(f => /UV/i.test(f))) issues.push({ title: 'UV equipment reports a fault', next: 'Inspect the UV equipment locally before reset.' });
  if (state?.local_interlock_state === 'TRIPPED' && !state.fault_codes.length) issues.push({ title: 'Device interlock is tripped', next: 'Inspect and resolve the device fault before resetting.' });
  if (/FAILED|TIMEOUT|EXPIRED/.test(status?.safety.command_status || '')) issues.push({ title: status!.safety.command_status.replace(/_/g, ' '), next: 'Review command delivery in Activity. Do not assume equipment has stopped.' });
  const unavailable = measurements.filter(m => state && (state[m.key] == null || (state.sensor_status?.[m.sensor] && state.sensor_status[m.sensor] !== 'OK')));
  if (unavailable.length) issues.push({ title: `${unavailable.map(m => m.label).join(', ')} unavailable`, next: 'Check the affected sensor or active missing-sensor simulation.' });
  const calibration = status?.calibration;
  if (state?.water_distance_cm != null && state.sensor_status?.water_distance === 'FAULT') issues.push({ title: 'Water-level sensor fault', next: 'Inspect the sensor. No valid level is available.' });
  else if (state && state.water_distance_cm == null) issues.push({ title: 'Water level unavailable', next: 'Check the ultrasonic sensor or active level simulation.' });
  else if (state?.water_distance_cm != null && !calibration) issues.push({ title: 'Water-level calibration required', next: 'Set measured empty and full endpoints in Device settings below.' });
  else if (state?.water_distance_cm != null && calibration && (state.water_distance_cm > calibration.empty_distance_cm || state.water_distance_cm < calibration.full_distance_cm)) issues.push({ title: 'Water distance outside calibrated range', next: 'Check the sensor placement and calibration. The drawing is clamped; the raw reading is retained.' });
  const flowHistory = history.slice(-36);
  const validFlow = flowHistory.filter(r => typeof r.flow_L_min === 'number' && Number.isFinite(r.flow_L_min));
  const maximum = Math.max(1, ...validFlow.map(r => r.flow_L_min));
  const firstTime = flowHistory.length ? Date.parse(flowHistory[0].received_at) : 0;
  const lastTime = flowHistory.length ? Date.parse(flowHistory[flowHistory.length - 1].received_at) : 0;
  const path = flowHistory.map((r, i) => {
    if (typeof r.flow_L_min !== 'number' || !Number.isFinite(r.flow_L_min)) return '';
    const previous = flowHistory[i - 1];
    const connected = previous && typeof previous.flow_L_min === 'number' && Date.parse(r.received_at) - Date.parse(previous.received_at) <= 15000;
    const x = lastTime > firstTime ? 8 + (Date.parse(r.received_at) - firstTime) / (lastTime - firstTime) * 604 : 310;
    return `${connected ? 'L' : 'M'}${x},${78 - r.flow_L_min / maximum * 58}`;
  }).join(' ');
  return <div className="overview-layout">
    <section className={`operating-summary tone-${summary.tone}`} aria-label="Operating status">
      <div><p className="eyebrow">Operating status</p><h2>{summary.title}</h2><p className="summary-note">{summary.note}</p></div>
      {simulated && <div className="operation-actions">
        <button className="button secondary" disabled={busy || !status?.safety.can_reset_simulator} onClick={onReset}>Reset test device</button>
        <button className="button primary" disabled={busy || !status?.is_fresh || !status?.safety.can_start_simulator} onClick={onStart}>Start simulated operation</button>
        <span>Reset leaves equipment stopped.</span>
      </div>}
    </section>

    <section className="monitoring-surface" aria-labelledby="readings-title">
      <div className="section-heading"><div><p className="eyebrow">Monitoring</p><h2 id="readings-title">Live readings</h2></div><span className="reading-freshness">{!state ? 'Waiting for first update' : status?.is_fresh ? 'Receiving updates' : 'Stale · last known readings'}</span></div>
      <div className="readings-composition">
        <div className="tank-area"><WaterLevelCylinder distance={state?.water_distance_cm} sensorStatus={state?.sensor_status?.water_distance} calibration={status?.calibration} fresh={Boolean(status?.is_fresh)} timestamp={state?.last_received_at} /></div>
        <dl className="measurement-grid">{measurements.map(item => {
          const value = state?.[item.key];
          const sensorState = state?.sensor_status?.[item.sensor];
          const missing = value == null || !Number.isFinite(value) || (sensorState && sensorState !== 'OK');
          return <div className="measurement" key={item.key}>
            <dt title={item.key}>{item.label}</dt>
            <dd><span className="measurement-value">{missing ? '—' : value!.toFixed(item.digits)}</span><span className="measurement-unit">{item.unit}</span></dd>
            <span className={`measurement-note ${missing && state ? 'unavailable' : ''}`}>{!state ? 'Awaiting reading' : missing ? 'Unavailable' : !status?.is_fresh ? 'Stale reading' : item.key === 'stagnation_hours' && simulated ? 'Simulated time without flow' : 'Device reading'}</span>
          </div>;
        })}</dl>
      </div>
      <div className="equipment-strip" aria-label="Reported equipment states"><span>Device reports</span>{[['Pump', state?.pump_state], ['Valve', state?.valve_state], ['UV', state?.uv_status], ['Interlock', state?.local_interlock_state]].map(([name, value]) => <span key={name}>{name} <strong>{value || 'Unknown'}</strong></span>)}{state && !status?.is_fresh && <span className="stale-label">Feedback stale</span>}</div>
      <WaterLevelSettings key={deviceId} deviceId={deviceId} calibration={status?.calibration} onSaved={onRefresh} />
    </section>

    <div className="overview-bottom">
      <section className="attention-surface" aria-labelledby="attention-title"><div className="section-heading"><h2 id="attention-title">Needs attention</h2><span className="quiet-count">{issues.length}</span></div>
        {issues.length ? <ul className="attention-list">{issues.map((issue, i) => <li key={i}><AlertCircle size={17}/><div><strong>{issue.title}</strong><p>{issue.next}</p></div></li>)}</ul> : <div className="empty-attention"><AttentionIcon size={22} style={{color: state && status?.is_fresh ? '#6b8277' : '#617174'}}/><strong>{state && status?.is_fresh ? 'No active alerts' : 'Waiting for device readings'}</strong><p>{state && status?.is_fresh ? 'Based on the latest reported state. This does not certify water safety.' : 'Fault and sensor checks become available when readings arrive.'}</p></div>}
        {simulated && issues.length > 0 && <button className="text-button" onClick={() => onNavigate('verification')}>Open Developer tools <ChevronRight size={15}/></button>}
      </section>
      <section className="history-surface" aria-labelledby="recent-title"><div className="section-heading"><div><h2 id="recent-title">Recent history</h2><p className="muted">Flow rate · L/min</p></div><button className="text-button" onClick={() => onNavigate('trends')}>Full trends <ArrowUpRight size={15}/></button></div>
        {validFlow.length > 1 ? <><svg viewBox="0 0 620 92" className="mini-trend" role="img" aria-label="Recent stored flow readings; breaks indicate missing readings or gaps"><path d="M8 78H612" stroke="#e1e5e7"/><path d={path} fill="none" stroke="#50758b" strokeWidth="2.5" strokeLinejoin="round"/></svg><div className="trend-labels"><span>{new Date(firstTime).toLocaleTimeString([], {hour:'2-digit',minute:'2-digit'})}</span><span>{new Date(lastTime).toLocaleTimeString([], {hour:'2-digit',minute:'2-digit'})}</span></div></> : <div className="history-empty">A trend appears after enough readings arrive.</div>}
        <div className="recent-activity-heading"><h3>Latest activity</h3><button className="text-button" onClick={() => onNavigate('controls')}>View activity <ChevronRight size={15}/></button></div>
        {events.length ? <ul className="recent-activity">{events.slice(0, 3).map(event => <li key={event.id ?? event.timestamp + event.message}><Clock3 size={14}/><span>{activityLabel(event, simulated)}</span><time>{new Date(event.timestamp).toLocaleTimeString([], {hour:'2-digit',minute:'2-digit'})}</time></li>)}</ul> : <p className="muted">No recorded activity for this device.</p>}
      </section>
    </div>
  </div>;
}
