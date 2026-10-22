import React, { useState, useEffect, useRef } from 'react';
import type { Calibration } from './WaterLevelCylinder';
import MonitoringOverview from './MonitoringOverview';
import WaterLevelSettings from './WaterLevelSettings';
import {
  AlertTriangle,
  CheckCircle2,
  Clock,
  Database,
  Download,
  Eye,
  FileText,
  Flame,
  HardDrive,
  Info,
  Layers,
  Microscope,
  Power,
  RefreshCw,
  Server,
  Shield,
  ShieldAlert,
  Thermometer,
  Upload,
  Waves,
  Zap
} from 'lucide-react';

interface DeviceState {
  water_distance_cm: number | null;
  sensor_status?: Record<string, string>;
  temp_C: number | null;
  pH: number | null;
  EC_uS_cm: number | null;
  turbidity_NTU: number | null;
  flow_L_min: number | null;
  stagnation_hours: number | null;
  pump_state: string;
  valve_state: string;
  uv_status: string;
  local_interlock_state: string;
  fault_codes: string[];
  data_origin: string;
  last_device_timestamp: string | null;
  last_received_at: string | null;
}

interface SafetySummary {
  broker_connected: boolean;
  manual_stop_latched: boolean;
  stop_reason: string | null;
  stop_generation?: number;
  last_reset_status?: string;
  reset_banner_message?: string;
  is_test_device?: boolean;
  can_reset_simulator?: boolean;
  can_start_simulator?: boolean;
  command_status: string;
  command_details: {
    command_id: string;
    command_type: string;
    origin: string;
    created_at: string;
    expires_at: string;
    acknowledged_at: string | null;
    failure_reason: string | null;
  } | null;
  physical_stop_verified: boolean;
  reported_states: {
    pump_state: string;
    valve_state: string;
    uv_status: string;
    local_interlock_state: string;
  };
  last_received_at: string | null;
}

export interface SystemStatus {
  calibration?: Calibration | null;
  device_id: string;
  state: DeviceState | null;
  safety: SafetySummary;
  is_fresh: boolean;
  elapsed_seconds: number | null;
  freshness_timeout_sec?: number;
  ai_health: {
    available: boolean;
    service_status?: string;
    version?: string;
  };
  predictions: {
    sensors: any;
    vision: any;
    composite: any;
  };
}

export default function App() {
  const [activeTab, setActiveTab] = useState<'overview' | 'trends' | 'samples' | 'ai' | 'controls' | 'verification'>('overview');
  const [deviceId, setDeviceId] = useState('MISTSAFE-ESP32-01');
  const [receivedStatus, setStatus] = useState<SystemStatus | null>(null);
  const [statusError, setStatusError] = useState<string | null>(null);
  const [now, setNow] = useState(Date.now());
  const selectedDevice = useRef(deviceId);
  selectedDevice.current = deviceId;
  const status = receivedStatus?.device_id === deviceId ? {
    ...receivedStatus,
    is_fresh: receivedStatus.is_fresh && Boolean(receivedStatus.state?.last_received_at)
      && now - Date.parse(receivedStatus.state!.last_received_at!) <= (receivedStatus.freshness_timeout_sec ?? 15) * 1000,
  } : null;
  useEffect(() => {
    const timer = setInterval(() => setNow(Date.now()), 1000);
    return () => clearInterval(timer);
  }, []);
  const [history, setHistory] = useState<any[]>([]);
  const [events, setEvents] = useState<any[]>([]);
  const [resetEvents, setResetEvents] = useState<any[]>([]);
  const [samples, setSamples] = useState<any[]>([]);
  const [loading, setLoading] = useState(false);
  const [shutdownPending, setShutdownPending] = useState(false);
  const [testOutput, setTestOutput] = useState<string | null>(null);
  const [testRunning, setTestRunning] = useState(false);
  const [testResult, setTestResult] = useState<'passed' | 'failed' | null>(null);
  const [actionMessage, setActionMessage] = useState<string | null>(null);

  const [simConfig, setSimConfig] = useState<any>({ enabled: 1 });
  const [selectedMissingSensor, setSelectedMissingSensor] = useState('temp_C');
  const [outOfLimitSensor, setOutOfLimitSensor] = useState('turbidity_NTU');
  const [outOfLimitValue, setOutOfLimitValue] = useState<number>(18.0);
  const [timeRange, setTimeRange] = useState<'1' | '6' | '24' | 'all'>('24');

  // Local Reset Simulation Form state
  const [rstEventId, setRstEventId] = useState(`rst-evt-${Date.now().toString().slice(-6)}`);
  const [rstBootId, setRstBootId] = useState('boot-esp32-alpha');
  const [rstSeq, setRstSeq] = useState<number>(1);
  const [rstOutcome, setRstOutcome] = useState<'SUCCESS' | 'FAILED'>('SUCCESS');
  const [rstFaults, setRstFaults] = useState<string>('');

  // Sample Upload Form state
  const [sampleId, setSampleId] = useState(`SMP-${Date.now().toString().slice(-6)}`);
  const [sampleLocation, setSampleLocation] = useState('Nozzle Manifold #1');
  const [sampleMethod, setSampleMethod] = useState('Brightfield 40x');
  const [sampleOrigin, setSampleOrigin] = useState<'REAL' | 'REFERENCE'>('REAL');
  const [labMethod, setLabMethod] = useState('');
  const [labResult, setLabResult] = useState('');
  const [imagePreview, setImagePreview] = useState<string | null>(null);
  const [selectedImage, setSelectedImage] = useState<File | null>(null);
  const [imageReading, setImageReading] = useState(false);
  const [imageError, setImageError] = useState<string | null>(null);
  const [sampleError, setSampleError] = useState<string | null>(null);
  const [samplesError, setSamplesError] = useState<string | null>(null);
  const fileReaderVersion = useRef(0);
  const fileInputRef = useRef<HTMLInputElement>(null);

  // Fetch status
  const fetchStatus = async () => {
    try {
      const res = await fetch(`/api/status?device_id=${deviceId}`);
      if (res.ok) {
        const data = await res.json();
        if (selectedDevice.current === deviceId) { setStatus(data); setStatusError(null); }
      } else if (selectedDevice.current === deviceId) setStatusError('Unable to retrieve device status');
    } catch (e) {
      if (selectedDevice.current === deviceId) setStatusError('Unable to reach the platform');
    }
  };

  // Fetch history
  const fetchHistory = async () => {
    try {
      const res = await fetch(`/api/history?device_id=${deviceId}&hours=${timeRange}`);
      if (res.ok) {
        const data = await res.json();
        if (selectedDevice.current === deviceId) setHistory(data);
      }
    } catch (e) {
      console.error('Fetch history error:', e);
    }
  };

  // Fetch simulator config
  const fetchSimConfig = async () => {
    try {
      const res = await fetch(`/api/simulator/config?device_id=${deviceId}`);
      if (res.ok) {
        const data = await res.json();
        if (selectedDevice.current === deviceId) setSimConfig(data);
      }
    } catch (e) {
      console.error('Fetch sim config error:', e);
    }
  };


  // Fetch events
  const fetchEvents = async () => {
    try {
      const res = await fetch(`/api/events?device_id=${deviceId}&severity=ALL`);
      if (res.ok) {
        const data = await res.json();
        if (selectedDevice.current === deviceId) setEvents(data);
      }
    } catch (e) {
      console.error('Fetch events error:', e);
    }
  };

  // Fetch samples
  const fetchSamples = async () => {
    try {
      const res = await fetch(`/api/samples?device_id=${deviceId}`);
      const data = await res.json();
      if (!res.ok || !Array.isArray(data)) throw new Error(data.error || 'Unable to retrieve saved samples');
      if (selectedDevice.current === deviceId) { setSamples(data); setSamplesError(null); }
      return data;
    } catch (e) {
      if (selectedDevice.current === deviceId) setSamplesError('Saved samples could not be retrieved. Your existing records may still be stored. Retry to check.');
    }
  };

  // Fetch reset events
  const fetchResetEvents = async () => {
    try {
      const res = await fetch(`/api/reset-events?device_id=${deviceId}`);
      if (res.ok) {
        const data = await res.json();
        if (selectedDevice.current === deviceId) setResetEvents(data);
      }
    } catch (e) {
      console.error('Fetch reset events error:', e);
    }
  };

  useEffect(() => {
    fetchStatus();
    fetchHistory();
    fetchEvents();
    fetchResetEvents();
    fetchSamples();
    if (deviceId.startsWith('MISTSAFE-SIM-')) {
      fetchSimConfig();
    }

    const interval = setInterval(() => {
      fetchStatus();
      if (activeTab === 'trends' || activeTab === 'overview') fetchHistory();
      if (activeTab === 'controls' || activeTab === 'overview') {
        fetchEvents();
        fetchResetEvents();
      }
      if (deviceId.startsWith('MISTSAFE-SIM-')) {
        fetchSimConfig();
      }
    }, 4000);

    return () => clearInterval(interval);
  }, [deviceId, activeTab, timeRange]);


  // Request Shutdown
  const handleShutdown = async () => {
    if (shutdownPending) return;
    setShutdownPending(true);
    setActionMessage(null);
    try {
      const res = await fetch('/api/shutdown', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          device_id: deviceId,
          reason: 'Manual emergency stop requested from operator console'
        })
      });
      const data = await res.json();
      if (!res.ok || data.success === false) {
        setActionMessage('Shutdown failed: ' + (data.message || data.error || 'Request rejected'));
      } else {
        setActionMessage(data.message || 'Shutdown requested. Await device acknowledgement and equipment feedback.');
      }
      await fetchStatus();
      await fetchEvents();
    } catch (e: any) {
      setActionMessage('Failed to request shutdown: ' + e.message);
    } finally {
      setShutdownPending(false);
    }
  };

  // Reset Test Device
  const handleSimulatorReset = async () => {
    setLoading(true);
    setActionMessage(null);
    try {
      const res = await fetch('/api/simulator/reset', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ device_id: deviceId })
      });
      const data = await res.json();
      if (data.success) {
        setActionMessage(data.message || 'Reset complete — stopped, ready for simulated start.');
      } else {
        setActionMessage('Reset rejected: ' + (data.message || data.error));
      }
      await fetchStatus();
      await fetchEvents();
      await fetchResetEvents();
    } catch (e: any) {
      setActionMessage('Reset error: ' + e.message);
    } finally {
      setLoading(false);
    }
  };

  // Start Simulated Operation
  const handleSimulatorStart = async () => {
    setLoading(true);
    setActionMessage(null);
    try {
      const res = await fetch('/api/simulator/start', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ device_id: deviceId })
      });
      const data = await res.json();
      if (data.success) {
        setActionMessage(data.message || 'Simulated operation started: Pump ON, Valve OPEN');
      } else {
        setActionMessage('Start rejected: ' + (data.message || data.error));
      }
      await fetchStatus();
      await fetchEvents();
    } catch (e: any) {
      setActionMessage('Start error: ' + e.message);
    } finally {
      setLoading(false);
    }
  };

  // Inject Simulator Faults
  const handleInjectFault = async (fault: string) => {
    setLoading(true);
    try {
      const response = await fetch('/api/simulator/faults', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ device_id: deviceId, faults: [fault] })
      });
      const result = await response.json();
      if (!response.ok || result.error || result.success === false) throw new Error(result.error || result.message || 'Fault injection failed');
      setActionMessage(`Injected simulated fault: ${fault} on ${deviceId}`);
      await fetchStatus();
    } catch (e: any) {
      setActionMessage('Fault injection error: ' + e.message);
    } finally {
      setLoading(false);
    }
  };

  // Clear Simulator Faults
  const handleClearFaults = async () => {
    setLoading(true);
    try {
      const res = await fetch('/api/simulator/clear-faults', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ device_id: deviceId })
      });
      const data = await res.json();
      setActionMessage(data.message || 'Cleared faults on test device');
      await fetchStatus();
      await fetchSimConfig();
    } catch (e: any) {
      setActionMessage('Clear faults error: ' + e.message);
    } finally {
      setLoading(false);
    }
  };

  // Inject Flow Loss
  const handleInjectFlowLoss = async () => {
    setLoading(true);
    try {
      const res = await fetch('/api/simulator/inject-flow-loss', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ device_id: deviceId })
      });
      const data = await res.json();
      setActionMessage(data.message || 'Flow loss injected (forced to 0.0, will fault after delay)');
      await fetchStatus();
      await fetchSimConfig();
    } catch (e: any) {
      setActionMessage('Flow loss error: ' + e.message);
    } finally {
      setLoading(false);
    }
  };

  // Inject Missing Sensor
  const handleInjectMissingSensor = async () => {
    setLoading(true);
    try {
      const res = await fetch('/api/simulator/inject-missing-sensor', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ device_id: deviceId, sensor: selectedMissingSensor })
      });
      const data = await res.json();
      setActionMessage(`Injected missing sensor: ${data.sensor} (reading published as null/unavailable)`);
      await fetchStatus();
      await fetchSimConfig();
    } catch (e: any) {
      setActionMessage('Missing sensor error: ' + e.message);
    } finally {
      setLoading(false);
    }
  };

  // Inject Out-Of-Limit Reading
  const handleInjectOutOfLimit = async () => {
    setLoading(true);
    try {
      const res = await fetch('/api/simulator/inject-out-of-limit', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          device_id: deviceId,
          sensor: outOfLimitSensor,
          value: outOfLimitValue
        })
      });
      const data = await res.json();
      setActionMessage(`Injected out-of-limit reading: ${data.sensor} = ${data.value}`);
      await fetchStatus();
      await fetchSimConfig();
    } catch (e: any) {
      setActionMessage('Out of limit error: ' + e.message);
    } finally {
      setLoading(false);
    }
  };

  // Pause / Resume Simulated Telemetry
  const handleTelemetryControl = async (mode: 'pause' | 'resume') => {
    setLoading(true);
    try {
      const res = await fetch('/api/simulator/telemetry-control', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ device_id: deviceId, mode })
      });
      const data = await res.json();
      setActionMessage(mode === 'pause'
        ? '⏸️ Simulated telemetry paused. No new records will be generated.'
        : '▶️ Simulated telemetry resumed. Reporting restored without clearing latches or restarting equipment.');
      await fetchSimConfig();
      await fetchStatus();
    } catch (e: any) {
      setActionMessage('Telemetry control error: ' + e.message);
    } finally {
      setLoading(false);
    }
  };


  // Process Local Reset Event Simulation
  const handleProcessLocalReset = async (e: React.FormEvent) => {
    e.preventDefault();
    setLoading(true);
    try {
      const faultList = rstFaults.split(',').map(s => s.trim()).filter(Boolean);
      const payload = {
        event_type: 'LOCAL_RESET',
        event_id: rstEventId,
        device_id: deviceId,
        boot_id: rstBootId,
        seq: rstSeq,
        timestamp: new Date().toISOString(),
        stop_command_id: status?.safety?.command_details?.command_id || '',
        stop_generation: status?.safety?.stop_generation || 1,
        outcome: rstOutcome,
        remaining_faults: faultList
      };
      const res = await fetch('/api/local-reset', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ device_id: deviceId, event_payload: payload })
      });
      const data = await res.json();
      if (data.success) {
        setActionMessage('✅ ' + (data.message || 'Local reset confirmed — equipment stopped.'));
        setRstEventId(`rst-evt-${Date.now().toString().slice(-6)}`);
        setRstSeq(prev => prev + 1);
      } else {
        setActionMessage('❌ ' + (data.message || 'Local reset event rejected'));
      }
      await fetchStatus();
      await fetchEvents();
      await fetchResetEvents();
    } catch (err: any) {
      setActionMessage('Local reset process error: ' + err.message);
    } finally {
      setLoading(false);
    }
  };

  // Analyse readings
  const handleRunInference = async () => {
    setLoading(true);
    setActionMessage(null);
    try {
      const res = await fetch('/api/predict', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ device_id: deviceId })
      });
      const data = await res.json();
      if (!res.ok || data.error) throw new Error(data.error || 'Analysis request failed');
      const results = [data.sensor_pred, data.vision_pred].filter(Boolean);
      setActionMessage(results.some(result => result.status === 'SUCCESS' && result.risk_score != null)
        ? 'Analysis result received. Review model status and limitations.'
        : 'AI analysis unavailable: ' + (data.sensor_pred?.reason || 'No valid model result received.'));
      await fetchStatus();
    } catch (e: any) {
      setActionMessage('Inference error: ' + e.message);
    } finally {
      setLoading(false);
    }
  };

  // Run Simulator
  const handleRunSimulator = async () => {
    setLoading(true);
    setActionMessage(null);
    try {
      const res = await fetch('/api/simulate', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ count: 5 })
      });
      const data = await res.json();
      setActionMessage(`Generated 5 synthetic packets for test device MISTSAFE-SIM-01.`);
      await fetchStatus();
      await fetchHistory();
    } catch (e: any) {
      setActionMessage('Simulator error: ' + e.message);
    } finally {
      setLoading(false);
    }
  };

  // Run Pytest Suite
  const handleRunTests = async () => {
    setTestRunning(true);
    setTestResult(null);
    setTestOutput('Running software checks…');
    try {
      const res = await fetch('/api/run-tests', { method: 'POST' });
      const data = await res.json();
      if (!res.ok) throw new Error(data.error || 'Test execution request failed');
      setTestResult(data.success === true && data.exitCode === 0 ? 'passed' : 'failed');
      setTestOutput(data.output || 'No output received.');
    } catch (e: any) {
      setTestOutput('Error executing tests: ' + e.message);
      setTestResult('failed');
    } finally {
      setTestRunning(false);
    }
  };

  // Handle Image File Selection
  const handleImageChange = (e: React.ChangeEvent<HTMLInputElement>) => {
    const version = ++fileReaderVersion.current;
    const file = e.target.files?.[0] || null;
    setSelectedImage(file); setImagePreview(null); setImageError(null); setSampleError(null); setImageReading(false);
    if (file) {
      if (!['image/png', 'image/jpeg'].includes(file.type) || !file.size || file.size > 10 * 1024 * 1024) {
        setImageError('Choose a PNG or JPEG image up to 10 MB.');
        return;
      }
      setImageReading(true);
      const reader = new FileReader();
      reader.onload = () => {
        if (fileReaderVersion.current === version) { setImagePreview(reader.result as string); setImageReading(false); }
      };
      reader.onerror = reader.onabort = () => { if (fileReaderVersion.current === version) { setImageError('The image could not be read. Choose it again.'); setImageReading(false); } };
      reader.readAsDataURL(file);
    }
  };

  // Submit Sample
  const handleSampleSubmit = async (e: React.FormEvent) => {
    e.preventDefault();
    if (loading) return;
    setSampleError(null);
    if (!imagePreview || imageReading || !selectedImage) {
      setImageError(imageReading ? 'Please wait for the image to finish reading.' : 'Choose a PNG or JPEG image before saving.');
      return;
    }
    setLoading(true);
    try {
      const res = await fetch('/api/upload-sample', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          sample_id: sampleId,
          device_id: deviceId,
          location: sampleLocation,
          method: sampleMethod,
          data_origin: sampleOrigin,
          image_base64: imagePreview,
          lab_method: labMethod,
          lab_result: labResult
        })
      });
      const data = await res.json();
      if (res.ok && data.success === true) {
        const retrieved = await fetchSamples();
        if (!retrieved?.some((sample: any) => sample.sample_id === sampleId)) {
          setSampleError('The server accepted the save, but retrieval could not be confirmed. Retry the saved-samples list before submitting again.');
          return;
        }
        setActionMessage(`Sample ${sampleId} saved and available in Saved samples.`);
        setImagePreview(null);
        setSelectedImage(null);
        if (fileInputRef.current) fileInputRef.current.value = '';
        setSampleId(`SMP-${Date.now().toString().slice(-6)}`);
      } else {
        setSampleError(data.error || 'The sample could not be saved. Your image and inputs have been retained.');
      }
    } catch (err: any) {
      setSampleError('Upload failed: ' + err.message + '. Your image and inputs have been retained.');
    } finally {
      setLoading(false);
    }
  };

  // Format readings
  const fmt = (val: number | null | undefined, unit: string, precision: number = 1) => {
    if (val === null || val === undefined) return '—';
    return `${val.toFixed(precision)} ${unit}`.trim();
  };

  const devState = status?.device_id === deviceId ? status.state : null;
  const safety = status?.safety;
  const isLatched = safety?.manual_stop_latched;
  const cmdStatus = safety?.command_status || 'NO ACTIVE COMMAND';

  return (
    <div className="min-h-screen bg-surface text-ink flex flex-col font-sans">
      <header className="app-header">
        <a className="brand" href="#" onClick={event => { event.preventDefault(); setActiveTab('overview'); }} aria-label="MistSafe AI overview"><span className="brand-mark"><Waves size={23}/></span><span><strong>MistSafe <span>AI</span></strong><small>Water monitoring · Prototype</small></span></a>
        <div className="header-device">
          <label htmlFor="device-select">Device</label>
          <select id="device-select" value={deviceId} disabled={loading || shutdownPending} onChange={event => {
            setDeviceId(event.target.value); setStatus(null); setStatusError(null); setHistory([]); setEvents([]); setSamples([]); setSamplesError(null); setResetEvents([]); setActionMessage(null);
          }}><option value="MISTSAFE-ESP32-01">MISTSAFE-ESP32-01 · Primary</option><option value="MISTSAFE-SIM-01">MISTSAFE-SIM-01 · Simulator</option></select>
          <span className={`origin-badge ${deviceId === 'MISTSAFE-SIM-01' ? 'simulated' : ''}`}>{deviceId === 'MISTSAFE-SIM-01' ? 'SIMULATED' : status?.is_fresh && devState?.data_origin === 'REAL' ? 'LIVE · REAL' : 'REAL DEVICE'}</span>
        </div>
        <div className="header-shutdown"><button className="button shutdown" onClick={handleShutdown} disabled={shutdownPending} aria-label="Request shutdown for selected device"><Power size={16}/>{shutdownPending ? 'Requesting shutdown…' : 'Request shutdown'}</button></div>
      </header>
      <div className="connection-strip"><span className={`connection-dot ${status?.is_fresh ? 'connected' : ''}`}/><span>{!devState ? 'Waiting for device' : status?.is_fresh ? 'Connected · receiving readings' : 'Connection lost · stale readings'}</span><span className="connection-time">{devState?.last_received_at ? `Last update ${new Date(devState.last_received_at).toLocaleTimeString()}` : 'No readings received'}</span><details className="connection-details"><summary>Connection details</summary><div>MQTT broker: {safety?.broker_connected ? 'Connected' : 'Disconnected'}<br/>Device freshness: {status?.is_fresh ? 'Fresh' : devState ? 'Stale' : 'No data'}<br/>Source: {deviceId === 'MISTSAFE-SIM-01' ? 'Simulator · direct storage path' : devState?.data_origin || 'No reported origin'}</div></details></div>
      <div className="navigation-bar"><nav aria-label="Main navigation">{[
        {id:'overview', label:'Overview', icon:Waves}, {id:'trends', label:'Trends', icon:Clock}, {id:'samples', label:'Samples', icon:Microscope}, {id:'ai', label:'AI analysis', icon:Layers}, {id:'controls', label:'Activity', icon:FileText}, {id:'verification', label:'Developer tools', icon:Server},
      ].map(tab => <button key={tab.id} aria-current={activeTab === tab.id ? 'page' : undefined} className={activeTab === tab.id ? 'active' : ''} onClick={() => setActiveTab(tab.id as any)}><tab.icon size={16}/>{tab.label}</button>)}</nav></div>

      {/* Main Tab Content */}
      <main className="app-main space-y-6">
        {actionMessage && <div role="status" className="action-feedback"><Info size={17}/><span>{actionMessage}</span><button aria-label="Dismiss message" onClick={() => setActionMessage(null)}>×</button></div>}

        {activeTab === 'controls' && <details className="device-details surface"><summary>Device & command details</summary>
        {/* 4 Independent Safety State Cards (Never combined into misleading single status) */}
        <div className="space-y-2">
          <div className="flex items-center justify-between text-xs text-muted">
            <span className="font-semibold uppercase tracking-wider text-ink">Independent Safety State Supervision</span>
            <span>A reset clears an eligible stop latch; it does NOT start equipment.</span>
          </div>

          <div className="grid grid-cols-1 md:grid-cols-4 gap-4">
            {/* 1. Platform Stop Latch */}
            <div className={`p-4 rounded-xl border ${
              isLatched ? 'bg-danger-soft border-danger-soft' : 'bg-surface border-line'
            }`}>
              <div className="text-xs text-muted mb-1 font-semibold">1. Platform Stop Latch</div>
              <div className="flex items-center gap-2">
                <span className={`w-2.5 h-2.5 rounded-full ${isLatched ? 'bg-danger ' : 'bg-emerald-400'}`}></span>
                <span className="font-bold text-sm font-mono text-ink">
                  {isLatched ? `LATCHED (Gen ${safety?.stop_generation || 1})` : 'No stop latch'}
                </span>
              </div>
              <p className="text-[11px] text-muted mt-1 truncate">
                {isLatched ? (safety?.stop_reason || 'Manual Operator Latch') : 'No platform stop latch active'}
              </p>
            </div>

            {/* 2. ESP32 Hardware Interlock */}
            <div className={`p-4 rounded-xl border ${
              safety?.reported_states.local_interlock_state === 'TRIPPED' ? 'bg-danger-soft border-danger-soft' : 'bg-surface border-line'
            }`}>
              <div className="text-xs text-muted mb-1 font-semibold">2. ESP32 Hardware Interlock</div>
              <div className="flex items-center gap-2">
                <Shield className="w-4 h-4 text-accent" />
                <span className="font-bold text-sm font-mono text-ink">
                  {safety?.reported_states.local_interlock_state || 'UNKNOWN'}
                </span>
              </div>
              <p className="text-[11px] text-muted mt-1">Interlock state reported by device</p>
            </div>

            {/* 3. Command Progress */}
            <div className="p-4 rounded-xl bg-surface border border-line">
              <div className="text-xs text-muted mb-1 font-semibold">3. Command Progress</div>
              <div className="flex items-center gap-2">
                <HardDrive className="w-4 h-4 text-accent" />
                <span className={`font-bold text-xs font-mono truncate ${
                  cmdStatus.includes('STOP') || cmdStatus.includes('FAILED') || cmdStatus.includes('TIMEOUT') ? 'text-danger' :
                  cmdStatus.includes('RESET') || cmdStatus.includes('START') ? 'text-warning' : 'text-ink'
                }`}>
                  {cmdStatus}
                </span>
              </div>
              {safety?.command_details?.failure_reason ? (
                <p className="text-[11px] text-danger mt-1 font-mono truncate" title={safety.command_details.failure_reason}>
                  ⚠️ {safety.command_details.failure_reason}
                </p>
              ) : (
                <p className="text-[11px] text-muted mt-1 font-mono truncate">
                  {safety?.command_details ? `ID: ${safety.command_details.command_id}` : 'No active queued commands'}
                </p>
              )}
            </div>

            {/* 4. Reported Equipment States */}
            <div className={`p-4 rounded-xl border ${
              !status?.is_fresh && devState ? 'bg-warning-soft border-warning-soft' : 'bg-surface border-line'
            }`}>
              <div className="text-xs text-muted mb-1 font-semibold flex items-center justify-between">
                <span>4. Reported Equipment States</span>
                {!status?.is_fresh && devState && (
                  <span className="text-[10px] text-warning font-mono font-bold">FEEDBACK STALE</span>
                )}
              </div>
              <div className="flex items-center justify-between text-xs font-mono font-bold text-ink">
                <span>Pump: <span className={!status?.is_fresh ? 'text-warning' : safety?.reported_states.pump_state === 'ON' ? 'text-confirmed' : 'text-danger'}>
                  {status?.is_fresh ? (safety?.reported_states.pump_state || 'UNKNOWN') : `${safety?.reported_states.pump_state || 'UNKNOWN'} (Stale)`}
                </span></span>
                <span>Valve: <span className={!status?.is_fresh ? 'text-warning' : safety?.reported_states.valve_state === 'OPEN' ? 'text-confirmed' : 'text-danger'}>
                  {status?.is_fresh ? (safety?.reported_states.valve_state || 'UNKNOWN') : `${safety?.reported_states.valve_state || 'UNKNOWN'} (Stale)`}
                </span></span>
              </div>
              <p className="text-[11px] text-muted mt-1">
                {!status?.is_fresh && devState
                  ? `⚠️ Last received ${status?.elapsed_seconds != null ? Math.round(status.elapsed_seconds || 0) : ''}s ago. Current operational state unconfirmed.`
                  : safety?.reported_states.pump_state === 'OFF' && safety?.reported_states.valve_state === 'CLOSED'
                  ? 'Reports pump OFF · valve CLOSED'
                  : 'Telemetry readback from GPIO'}
              </p>
            </div>

          </div>
        </div>

        </details>}
        {activeTab === 'verification' && <>
        {/* Operational Reset Banner */}
        <div className={`p-4 rounded-xl border flex flex-wrap items-center justify-between gap-3 text-xs ${
          isLatched
            ? 'bg-danger-soft border-danger-soft text-danger'
            : safety?.last_reset_status === 'LOCAL_RESET_CONFIRMED' || safety?.last_reset_status === 'RESET_COMPLETE'
            ? 'bg-confirmed-soft border-line text-confirmed'
            : 'bg-surface border-line text-ink'
        }`}>
          <div className="flex items-center gap-3">
            <AlertTriangle className={`w-5 h-5 shrink-0 ${isLatched ? 'text-danger' : 'text-confirmed'}`} />
            <div>
              <div className="font-bold text-sm">
                {isLatched
                  ? (deviceId.startsWith('MISTSAFE-SIM-')
                      ? 'Test device stopped. Awaiting simulator reset.'
                      : 'Resolve the fault and reset locally on the ESP32. Waiting for device confirmation.')
                  : (safety?.last_reset_status === 'LOCAL_RESET_CONFIRMED'
                      ? 'Local reset confirmed — equipment stopped.'
                      : safety?.last_reset_status === 'RESET_COMPLETE'
                      ? 'Reset complete — stopped, ready for simulated start.'
                      : 'No manual stop latch active. Check device feedback before starting.')}
              </div>
              <div className="text-[11px] opacity-80 mt-0.5">
                {deviceId.startsWith('MISTSAFE-SIM-')
                  ? 'Simulator controls allow verified bench testing of latch release and separate start.'
                  : 'Remote reset and remote start are permanently disabled for real hardware. Physical push-button reset required.'}
              </div>
            </div>
          </div>

          {/* Action buttons embedded in banner */}
          <div className="flex items-center gap-2">
            {deviceId.startsWith('MISTSAFE-SIM-') ? (
              <>
                <button
                  onClick={handleSimulatorReset}
                  disabled={loading || !isLatched}
                  className={`px-3 py-1.5 rounded font-semibold text-xs transition flex items-center gap-1.5 ${
                    isLatched
                      ? 'bg-warning hover:bg-warning text-white '
                      : 'bg-subtle text-muted cursor-not-allowed border border-line'
                  }`}
                  title="Clears platform stop latch on test simulator only after verifying 0 faults. Leaves pump OFF and valve CLOSED."
                >
                  <RefreshCw className="w-3.5 h-3.5" />
                  Reset test device
                </button>

                <button
                  onClick={handleSimulatorStart}
                  disabled={loading || isLatched || safety?.reported_states.pump_state === 'ON'}
                  className={`px-3 py-1.5 rounded font-semibold text-xs transition flex items-center gap-1.5 ${
                    !isLatched && safety?.reported_states.pump_state !== 'ON'
                      ? 'bg-confirmed hover:bg-confirmed text-white '
                      : 'bg-subtle text-muted cursor-not-allowed border border-line'
                  }`}
                  title="Separate explicit action. Requires successful reset, fresh feedback, and zero faults."
                >
                  <Power className="w-3.5 h-3.5" />
                  Start simulated operation
                </button>
              </>
            ) : (
              <span className="text-muted text-xs italic bg-subtle px-2.5 py-1 rounded border border-line">
                Remote reset/start disabled on real hardware
              </span>
            )}
          </div>
        </div>

        {/* Simulator Controls & Scenario Injection Panel (Only visible for test devices) */}
        {deviceId.startsWith('MISTSAFE-SIM-') && (
          <div className="p-4 rounded-xl bg-surface border border-line text-xs space-y-3">
            <div className="flex flex-wrap items-center justify-between gap-3 border-b border-line pb-2.5">
              <div className="flex items-center gap-2 text-ink">
                <span className="font-semibold text-warning">🧪 Simulation controls</span>
                <span className="text-muted">Current scenario: <span className="font-semibold text-accent font-mono">Controlled test data</span> (Not water safety limits)</span>
              </div>
              <div className="flex items-center gap-2">
                <span className="text-muted">Reading generator:</span>
                {simConfig?.enabled === 0 ? (
                  <span className="px-2 py-0.5 rounded bg-warning-soft text-warning border border-warning-soft font-mono font-bold">
                    PAUSED
                  </span>
                ) : (
                  <span className="px-2 py-0.5 rounded bg-confirmed-soft text-confirmed border border-line font-mono font-bold flex items-center gap-1.5">
                    <span className="w-1.5 h-1.5 rounded-full bg-emerald-400 "></span>
                    ACTIVE (1 msg / 5s)
                  </span>
                )}
                {simConfig?.enabled === 0 ? (
                  <button
                    onClick={() => handleTelemetryControl('resume')}
                    disabled={loading}
                    className="px-2.5 py-1 bg-confirmed hover:bg-confirmed text-white font-semibold rounded  flex items-center gap-1"
                  >
                    ▶️ Resume readings
                  </button>
                ) : (
                  <button
                    onClick={() => handleTelemetryControl('pause')}
                    disabled={loading}
                    className="px-2.5 py-1 bg-warning hover:bg-warning text-white font-semibold rounded  flex items-center gap-1"
                  >
                    ⏸️ Pause readings
                  </button>
                )}
              </div>
            </div>

            {/* Test Scenarios Injection Grid */}
            <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-4 gap-3 pt-1">
              {/* Scenario 1 & 2: UV Fault & Flow Loss */}
              <div className="bg-surface p-2.5 rounded-lg border border-line space-y-2">
                <div className="font-semibold text-ink">Hardware & Hydraulic Faults</div>
                <div className="flex flex-wrap gap-2">
                  <button
                    onClick={() => handleInjectFault('E02_UV_FAIL')}
                    disabled={loading}
                    className="px-2.5 py-1 bg-subtle hover:bg-subtle text-warning rounded border border-line w-full text-left flex items-center justify-between"
                  >
                    <span>Inject UV Fault</span>
                    <span className="text-[10px] text-muted font-mono">Trip local interlock</span>
                  </button>
                  <button
                    onClick={handleInjectFlowLoss}
                    disabled={loading}
                    className="px-2.5 py-1 bg-subtle hover:bg-subtle text-warning rounded border border-line w-full text-left flex items-center justify-between"
                  >
                    <span>Inject Flow Loss</span>
                    <span className="text-[10px] text-muted font-mono">Flow=0 → Delay → Fault</span>
                  </button>
                </div>
              </div>

              {/* Scenario 3: Missing Sensor */}
              <div className="bg-surface p-2.5 rounded-lg border border-line space-y-2">
                <div className="font-semibold text-ink">Inject Missing Sensor (null)</div>
                <div className="flex items-center gap-2">
                  <select
                    value={selectedMissingSensor}
                    onChange={(e) => setSelectedMissingSensor(e.target.value)}
                    className="bg-subtle border border-line rounded px-2 py-1 text-ink w-full"
                  >
                    <option value="temp_C">Temperature (temp_C)</option>
                    <option value="pH">pH (pH)</option>
                    <option value="EC_uS_cm">Conductivity (EC_uS_cm)</option>
                    <option value="turbidity_NTU">Turbidity (turbidity_NTU)</option>
                    <option value="flow_L_min">Flow Rate (flow_L_min)</option>
                    <option value="stagnation_hours">Time without flow (stagnation_hours)</option>
                  </select>
                  <button
                    onClick={handleInjectMissingSensor}
                    disabled={loading}
                    className="px-2.5 py-1 bg-subtle hover:bg-subtle text-accent rounded border border-line shrink-0"
                  >
                    Inject
                  </button>
                </div>
              </div>

              {/* Scenario 4: Out-Of-Limit Reading */}
              <div className="bg-surface p-2.5 rounded-lg border border-line space-y-2">
                <div className="font-semibold text-ink">Inject Out-of-Limit Reading</div>
                <div className="flex items-center gap-2">
                  <select
                    value={outOfLimitSensor}
                    onChange={(e) => {
                      setOutOfLimitSensor(e.target.value);
                      if (e.target.value === 'turbidity_NTU') setOutOfLimitValue(18.0);
                      else if (e.target.value === 'temp_C') setOutOfLimitValue(55.0);
                      else if (e.target.value === 'pH') setOutOfLimitValue(11.0);
                      else if (e.target.value === 'EC_uS_cm') setOutOfLimitValue(2500.0);
                    }}
                    className="bg-subtle border border-line rounded px-2 py-1 text-ink w-1/2"
                  >
                    <option value="turbidity_NTU">Turbidity (&gt;15 NTU)</option>
                    <option value="temp_C">Temp (&gt;50 °C)</option>
                    <option value="pH">pH (&gt;10)</option>
                    <option value="EC_uS_cm">EC (&gt;2000 µS)</option>
                  </select>
                  <input
                    type="number"
                    value={outOfLimitValue}
                    onChange={(e) => setOutOfLimitValue(parseFloat(e.target.value))}
                    className="bg-subtle border border-line rounded px-2 py-1 text-ink w-1/4 font-mono text-center"
                  />
                  <button
                    onClick={handleInjectOutOfLimit}
                    disabled={loading}
                    className="px-2.5 py-1 bg-subtle hover:bg-subtle text-danger rounded border border-line shrink-0"
                  >
                    Inject
                  </button>
                </div>
              </div>

              {/* Scenario 5: Clear Simulator Faults */}
              <div className="bg-surface p-2.5 rounded-lg border border-line space-y-2">
                <div className="font-semibold text-ink">Fault Clearance Control</div>
                <button
                  onClick={handleClearFaults}
                  disabled={loading}
                  className="w-full py-2 bg-confirmed hover:bg-confirmed text-white font-semibold rounded border border-line text-center flex items-center justify-center gap-1.5 "
                >
                  <CheckCircle2 className="w-3.5 h-3.5" />
                  Clear Simulator Faults
                </button>
                <div className="text-[10px] text-muted text-center">
                  Leaves equipment stopped and latched; requires Reset then Start.
                </div>
              </div>
            </div>
          </div>
        )}


        </>}
        {activeTab === 'overview' && <MonitoringOverview deviceId={deviceId} status={status} statusError={statusError} events={events} history={history} busy={loading || shutdownPending} onReset={handleSimulatorReset} onStart={handleSimulatorStart} onRefresh={fetchStatus} onNavigate={setActiveTab} />}

        {/* ---------------------------------------------------- */}
        {/* VIEW 2: TRENDS */}
        {/* ---------------------------------------------------- */}
        {activeTab === 'trends' && (
          <div className="space-y-6">
            <div className="flex flex-wrap items-center justify-between gap-4">
              <div>
                <h3 className="text-base font-bold text-ink flex items-center gap-2">
                  <Clock className="w-5 h-5 text-accent" />
                  Reading history
                </h3>
                <p className="text-xs text-muted">
                  Stored readings with gaps for missing data. Up to 500 loaded records; CSV exports the loaded records only.
                </p>
              </div>

              <div className="flex items-center gap-3">
                {/* Time range selector */}
                <div className="flex items-center gap-1.5 bg-surface border border-line rounded-lg p-1 text-xs">
                  <span className="text-muted px-2">Range:</span>
                  {(['1', '6', '24', 'all'] as const).map((r) => (
                    <button
                      key={r}
                      onClick={() => setTimeRange(r)}
                      className={`px-2.5 py-1 rounded font-medium transition ${
                        timeRange === r
                          ? 'bg-accent text-white '
                          : 'text-muted hover:text-ink'
                      }`}
                    >
                      {r === '1' ? '1h' : r === '6' ? '6h' : r === '24' ? '24h' : 'All'}
                    </button>
                  ))}
                </div>

                {history.length > 0 && (
                  <button
                    onClick={() => {
                      const csvContent = 'data:text/csv;charset=utf-8,' +
                        ['id,device_id,timestamp,received_at,data_origin,temp_C,pH,EC_uS_cm,turbidity_NTU,flow_L_min,stagnation_hours']
                          .concat(history.map(r => `${r.id},${r.device_id},${r.timestamp},${r.received_at},${r.data_origin},${r.temp_C !== null && r.temp_C !== undefined ? r.temp_C : ''},${r.pH !== null && r.pH !== undefined ? r.pH : ''},${r.EC_uS_cm !== null && r.EC_uS_cm !== undefined ? r.EC_uS_cm : ''},${r.turbidity_NTU !== null && r.turbidity_NTU !== undefined ? r.turbidity_NTU : ''},${r.flow_L_min !== null && r.flow_L_min !== undefined ? r.flow_L_min : ''},${r.stagnation_hours !== null && r.stagnation_hours !== undefined ? r.stagnation_hours : ''}`))
                          .join('\n');
                      const encodedUri = encodeURI(csvContent);
                      const link = document.createElement('a');
                      link.setAttribute('href', encodedUri);
                      link.setAttribute('download', `telemetry_${deviceId}_${Date.now()}.csv`);
                      document.body.appendChild(link);
                      link.click();
                      link.remove();
                    }}
                    className="px-3 py-1.5 bg-subtle hover:bg-subtle text-ink border border-line rounded-lg text-xs flex items-center gap-2 "
                  >
                    <Download className="w-3.5 h-3.5" />
                    Export loaded records ({history.length})
                  </button>
                )}
              </div>
            </div>

            {/* Simulated Data Badge Indicator */}
            {deviceId.startsWith('MISTSAFE-SIM-') && (
              <div className="bg-warning-soft border border-warning-soft text-warning p-2.5 rounded-lg text-xs flex items-center gap-2">
                <Info className="w-4 h-4 shrink-0 text-warning" />
                <span>
                  <strong>SIMULATED DATASET:</strong> Plotted trends originate strictly from test device <span className="font-mono text-accent">{deviceId}</span>. Gaps appear where readings were null/injected missing or when telemetry was paused.
                </span>
              </div>
            )}

            {history.length === 0 ? (
              <div className="p-12 rounded-xl bg-surface border border-line text-center space-y-3">
                <Clock className="w-10 h-10 text-muted mx-auto" />
                <h4 className="text-sm font-semibold text-ink">No historical records available for {deviceId}</h4>
                <p className="text-xs text-muted max-w-md mx-auto">
                  {deviceId.startsWith('MISTSAFE-SIM-')
                    ? 'Start simulated operation or wait for background periodic generator (every 5s) to record data.'
                    : 'Awaiting live telemetry from physical ESP32 node. Graphs will plot dynamically once packets are received.'}
                </p>
              </div>
            ) : (
              <div className="space-y-6">
                {/* 6 Time-Series Charts Grid */}
                <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 gap-4">
                  {[
                    { key: 'temp_C', title: 'Temperature', unit: '°C', color: '#9a8057', precision: 2, defaultMin: 20, defaultMax: 30 },
                    { key: 'pH', title: 'pH Level', unit: 'pH', color: '#5c7b91', precision: 2, defaultMin: 6.5, defaultMax: 8.5 },
                    { key: 'EC_uS_cm', title: 'Conductivity', unit: 'µS/cm', color: '#658875', precision: 1, defaultMin: 350, defaultMax: 550 },
                    { key: 'turbidity_NTU', title: 'Turbidity', unit: 'NTU', color: '#71919a', precision: 2, defaultMin: 0.5, defaultMax: 3.5 },
                    { key: 'flow_L_min', title: 'Flow Rate', unit: 'L/min', color: '#738398', precision: 2, defaultMin: 0, defaultMax: 20 },
                    { key: 'stagnation_hours', title: 'Time without flow', unit: 'hrs', color: '#8c7d91', precision: 1, defaultMin: 0, defaultMax: 10 }
                  ].map((cfg) => {
                    const validVals = history.map(r => r[cfg.key]).filter((v): v is number => v !== null && v !== undefined && typeof v === 'number');
                    const minVal = validVals.length > 0 ? Math.min(...validVals, cfg.defaultMin) : cfg.defaultMin;
                    const maxVal = validVals.length > 0 ? Math.max(...validVals, cfg.defaultMax) : cfg.defaultMax;
                    const valRange = maxVal - minVal || 1;

                    // Chart dimensions
                    const svgW = 380;
                    const svgH = 140;
                    const padL = 38;
                    const padR = 12;
                    const padT = 15;
                    const padB = 25;
                    const plotW = svgW - padL - padR;
                    const plotH = svgH - padT - padB;

                    const sortedHist = [...history].sort((a, b) => new Date(a.received_at).getTime() - new Date(b.received_at).getTime());
                    const tStart = sortedHist.length > 0 ? new Date(sortedHist[0].received_at).getTime() : 0;
                    const tEnd = sortedHist.length > 0 ? new Date(sortedHist[sortedHist.length - 1].received_at).getTime() : 1;
                    const tRange = Math.max(tEnd - tStart, 1000);

                    // Build path segments with GAP preservation:
                    // If reading is null or gap between successive points > 25 seconds, break path into new segment
                    const segments: { x: number; y: number }[][] = [];
                    let curSegment: { x: number; y: number }[] = [];

                    sortedHist.forEach((item, i) => {
                      const val = item[cfg.key];
                      const t = new Date(item.received_at).getTime();
                      const prevT = i > 0 ? new Date(sortedHist[i - 1].received_at).getTime() : t;

                      if (val === null || val === undefined || (i > 0 && t - prevT > 25000)) {
                        if (curSegment.length > 0) {
                          segments.push(curSegment);
                          curSegment = [];
                        }
                      }

                      if (val !== null && val !== undefined && typeof val === 'number') {
                        const x = padL + ((t - tStart) / tRange) * plotW;
                        const y = padT + plotH - ((val - minVal) / valRange) * plotH;
                        curSegment.push({ x, y });
                      }
                    });
                    if (curSegment.length > 0) segments.push(curSegment);

                    const latestVal = sortedHist.length > 0 ? sortedHist[sortedHist.length - 1][cfg.key] : null;

                    return (
                      <div key={cfg.key} className="p-4 rounded-xl bg-surface border border-line  flex flex-col justify-between">
                        <div className="flex items-center justify-between mb-1">
                          <span className="text-xs font-semibold text-ink">{cfg.title}</span>
                          <span className="text-xs font-mono font-bold" style={{ color: cfg.color }}>
                            {fmt(latestVal, cfg.unit, cfg.precision)}
                          </span>
                        </div>
                        <div className="text-[11px] text-muted mb-2">
                          Unit: <span className="font-mono text-muted">{cfg.unit}</span> | Min: {minVal.toFixed(1)} | Max: {maxVal.toFixed(1)}
                        </div>

                        {/* Responsive SVG Chart */}
                        <div className="w-full bg-surface rounded border border-line p-1 overflow-hidden">
                          <svg viewBox={`0 0 ${svgW} ${svgH}`} className="w-full h-28 overflow-visible">
                            {/* Grid lines */}
                            <line x1={padL} y1={padT} x2={svgW - padR} y2={padT} stroke="#1e293b" strokeDasharray="3 3" />
                            <line x1={padL} y1={padT + plotH / 2} x2={svgW - padR} y2={padT + plotH / 2} stroke="#1e293b" strokeDasharray="3 3" />
                            <line x1={padL} y1={padT + plotH} x2={svgW - padR} y2={padT + plotH} stroke="#334155" />

                            {/* Y Axis Labels */}
                            <text x={padL - 4} y={padT + 4} textAnchor="end" fontSize="9" fill="#64748b" fontFamily="monospace">
                              {maxVal.toFixed(cfg.precision > 1 ? 1 : 0)}
                            </text>
                            <text x={padL - 4} y={padT + plotH / 2 + 3} textAnchor="end" fontSize="9" fill="#64748b" fontFamily="monospace">
                              {((minVal + maxVal) / 2).toFixed(cfg.precision > 1 ? 1 : 0)}
                            </text>
                            <text x={padL - 4} y={padT + plotH} textAnchor="end" fontSize="9" fill="#64748b" fontFamily="monospace">
                              {minVal.toFixed(cfg.precision > 1 ? 1 : 0)}
                            </text>

                            {/* Plotted line segments (with gap preservation) */}
                            {segments.map((seg, sIdx) => {
                              if (seg.length === 0) return null;
                              if (seg.length === 1) {
                                return (
                                  <circle
                                    key={sIdx}
                                    cx={seg[0].x}
                                    cy={seg[0].y}
                                    r={3}
                                    fill={cfg.color}
                                  />
                                );
                              }
                              const pathD = seg.map((p, pIdx) => `${pIdx === 0 ? 'M' : 'L'} ${p.x.toFixed(1)} ${p.y.toFixed(1)}`).join(' ');
                              return (
                                <g key={sIdx}>
                                  <path
                                    d={pathD}
                                    fill="none"
                                    stroke={cfg.color}
                                    strokeWidth="2"
                                    strokeLinecap="round"
                                    strokeLinejoin="round"
                                  />
                                  {seg.map((p, pIdx) => (
                                    <circle
                                      key={pIdx}
                                      cx={p.x}
                                      cy={p.y}
                                      r={2}
                                      fill={cfg.color}
                                    />
                                  ))}
                                </g>
                              );
                            })}

                            {/* X Axis Time Labels */}
                            {sortedHist.length > 0 && (
                              <>
                                <text x={padL} y={svgH - 6} textAnchor="start" fontSize="9" fill="#64748b" fontFamily="monospace">
                                  {new Date(sortedHist[0].received_at).toLocaleTimeString([], { hour: '2-digit', minute: '2-digit', second: '2-digit' })}
                                </text>
                                <text x={svgW - padR} y={svgH - 6} textAnchor="end" fontSize="9" fill="#64748b" fontFamily="monospace">
                                  {new Date(sortedHist[sortedHist.length - 1].received_at).toLocaleTimeString([], { hour: '2-digit', minute: '2-digit', second: '2-digit' })}
                                </text>
                              </>
                            )}
                          </svg>
                        </div>
                      </div>
                    );
                  })}
                </div>

                {/* Raw Telemetry Records Table */}
                <div className="rounded-xl bg-surface border border-line overflow-hidden">
                  <div className="p-3 bg-subtle border-b border-line flex items-center justify-between text-xs">
                    <span className="font-semibold text-ink">Recent Telemetry Ingestion Log (Last 20)</span>
                    <span className="text-muted font-mono">Total {history.length} records</span>
                  </div>
                  <div className="overflow-x-auto max-h-72">
                    <table className="w-full text-xs text-left">
                      <thead className="bg-subtle text-muted font-mono uppercase sticky top-0">
                        <tr>
                          <th className="p-2.5">Received (UTC)</th>
                          <th className="p-2.5">Origin</th>
                          <th className="p-2.5">Temp (°C)</th>
                          <th className="p-2.5">pH</th>
                          <th className="p-2.5">EC (µS/cm)</th>
                          <th className="p-2.5">Turbidity (NTU)</th>
                          <th className="p-2.5">Flow (L/min)</th>
                          <th className="p-2.5">Time without flow (h)</th>
                        </tr>
                      </thead>
                      <tbody className="divide-y divide-line font-mono">
                        {history.slice(-20).reverse().map((row) => (
                          <tr key={row.id} className="hover:bg-subtle">
                            <td className="p-2.5 text-muted">{row.received_at}</td>
                            <td className="p-2.5">
                              <span className={`px-1.5 py-0.5 rounded text-[10px] ${
                                row.data_origin === 'SIMULATED' ? 'bg-warning-soft text-warning border border-warning-soft' : 'bg-accent-soft text-accent border border-line'
                              }`}>
                                {row.data_origin}
                              </span>
                            </td>
                            <td className="p-2.5 text-ink">{fmt(row.temp_C, '')}</td>
                            <td className="p-2.5 text-ink">{fmt(row.pH, '', 2)}</td>
                            <td className="p-2.5 text-ink">{fmt(row.EC_uS_cm, '', 1)}</td>
                            <td className="p-2.5 text-ink">{fmt(row.turbidity_NTU, '', 2)}</td>
                            <td className="p-2.5 text-ink">{fmt(row.flow_L_min, '', 2)}</td>
                            <td className="p-2.5 text-ink">{fmt(row.stagnation_hours, '', 1)}</td>
                          </tr>
                        ))}
                      </tbody>
                    </table>
                  </div>
                </div>
              </div>
            )}
          </div>
        )}


        {/* ---------------------------------------------------- */}
        {/* VIEW 3: SAMPLES & MICROSCOPY */}
        {/* ---------------------------------------------------- */}
        {activeTab === 'samples' && (
          <div className="grid grid-cols-1 lg:grid-cols-2 gap-6">
            {/* Upload Form */}
            <div className="p-5 rounded-xl bg-surface border border-line space-y-4">
              <div className="border-b border-line pb-3">
                <h3 className="text-sm font-bold text-ink flex items-center gap-2">
                  <Upload className="w-4 h-4 text-accent" />
                  Save a microscopy sample
                </h3>
                <p className="text-xs text-muted mt-0.5">Choose an image, document its source, then save it for this device.</p>
              </div>

              <form onSubmit={handleSampleSubmit} className="space-y-3 text-xs"><fieldset disabled={loading} className="space-y-3">
                <div>
                  <label htmlFor="sample-image" className="text-muted block mb-1">Choose image · PNG or JPEG, up to 10 MB</label>
                  <input
                    id="sample-image"
                    ref={fileInputRef}
                    type="file"
                    disabled={loading}
                    aria-describedby="image-feedback"
                    accept="image/png,image/jpeg"
                    onChange={handleImageChange}
                    className="w-full bg-subtle border border-line rounded p-2 text-ink file:mr-3 file:py-1 file:px-2 file:rounded file:border-0 file:bg-accent file:text-white file:text-xs"
                  />
                  <div id="image-feedback" aria-live="polite">
                    {selectedImage && <p className="sample-file-info">{selectedImage.name} · {(selectedImage.size / 1024).toFixed(1)} KB {imageReading ? '· Reading image…' : imagePreview ? '· Ready to save' : ''}</p>}
                    {imageError && <p className="field-error">{imageError}</p>}
                  </div>
                  {imagePreview && (
                    <div className="mt-2 p-2 bg-surface rounded border border-line">
                      <img src={imagePreview} alt="Preview" className="max-h-40 rounded mx-auto" />
                    </div>
                  )}
                </div>


                <div>
                  <label htmlFor="field-1" className="text-muted block mb-1">Sample ID</label>
                  <input id="field-1"
                    type="text"
                    value={sampleId}
                    onChange={(e) => setSampleId(e.target.value)}
                    className="w-full bg-subtle border border-line rounded p-2 text-ink font-mono"
                    required
                  />
                </div>

                <div className="grid grid-cols-2 gap-3">
                  <div>
                    <label htmlFor="field-2" className="text-muted block mb-1">Sampling Location</label>
                    <input id="field-2"
                      type="text"
                      value={sampleLocation}
                      onChange={(e) => setSampleLocation(e.target.value)}
                      className="w-full bg-subtle border border-line rounded p-2 text-ink"
                      required
                    />
                  </div>
                  <div>
                    <label htmlFor="field-3" className="text-muted block mb-1">Imaging Method</label>
                    <select id="field-3"
                      value={sampleMethod}
                      onChange={(e) => setSampleMethod(e.target.value)}
                      className="w-full bg-subtle border border-line rounded p-2 text-ink"
                    >
                      <option>Brightfield 40x</option>
                      <option>Phase Contrast 100x</option>
                      <option>Epifluorescence</option>
                      <option>Digital Holographic</option>
                      <option>Other / Not applicable</option>
                    </select>
                  </div>
                </div>

                <div>
                  <label htmlFor="field-4" className="text-muted block mb-1">Data Origin (Safety Classification)</label>
                  <select id="field-4"
                    value={sampleOrigin}
                    onChange={(e) => setSampleOrigin(e.target.value as any)}
                    className="w-full bg-subtle border border-line rounded p-2 text-ink"
                  >
                    <option value="REAL">REAL (Collected physical sample)</option>
                    <option value="REFERENCE">REFERENCE (Test or reference image)</option>
                  </select>
                  {sampleOrigin === 'REFERENCE' && (
                    <p className="text-[11px] text-warning mt-1">
                      ⚠️ Reference images are historical benchmarks and will NEVER trigger live hardware stop commands.
                    </p>
                  )}
                </div>

                <div className="grid grid-cols-2 gap-3">
                  <div>
                    <label htmlFor="field-5" className="text-muted block mb-1">Lab method (optional)</label>
                    <input id="field-5"
                      type="text"
                      placeholder="ISO 11731 Culture / qPCR"
                      value={labMethod}
                      onChange={(e) => setLabMethod(e.target.value)}
                      className="w-full bg-subtle border border-line rounded p-2 text-ink"
                    />
                  </div>
                  <div>
                    <label htmlFor="field-6" className="text-muted block mb-1">Lab result (optional)</label>
                    <input id="field-6"
                      type="text"
                      placeholder="e.g. <100 CFU/L"
                      value={labResult}
                      onChange={(e) => setLabResult(e.target.value)}
                      className="w-full bg-subtle border border-line rounded p-2 text-ink"
                    />
                  </div>
                </div>

                <button
                  type="submit"
                  disabled={loading || imageReading}
                  className="w-full py-2 bg-accent hover:bg-accent text-white font-medium rounded transition"
                >
                  {loading ? 'Saving…' : 'Save sample'}
                </button>
                {sampleError && <p role="alert" className="field-error">{sampleError}</p>}
                </fieldset>
              </form>
            </div>

            {/* Recorded Samples */}
            <div className="p-5 rounded-xl bg-surface border border-line space-y-4">
              <div className="border-b border-line pb-3">
                <h3 className="text-sm font-bold text-ink flex items-center gap-2">
                  <Database className="w-4 h-4 text-confirmed" />
                  Saved samples
                </h3>
                <p className="text-xs text-muted mt-0.5">Images and metadata saved for the selected device.</p>
              </div>

              {samplesError && <div role="alert" className="field-error">{samplesError}<button type="button" className="text-button" onClick={fetchSamples}>Retry saved samples</button></div>}
              {!samplesError && samples.length === 0 ? (
                <div className="p-8 text-center text-xs text-muted">
                  No samples saved for this device. Choose an image and save its metadata to get started.
                </div>
              ) : (
                <div className="space-y-4 max-h-[500px] overflow-y-auto pr-1">
                  {samples.map((s) => (
                    <div key={s.sample_id} className="p-3.5 rounded-lg bg-subtle border border-line space-y-2 text-xs">
                      <div className="flex items-center justify-between">
                        <span className="font-mono font-bold text-accent">{s.sample_id}</span>
                        <span className={`px-2 py-0.5 rounded font-mono text-[10px] font-bold ${
                          s.data_origin === 'REFERENCE' ? 'bg-warning-soft text-warning border border-warning-soft' : 'bg-confirmed-soft text-confirmed border border-line'
                        }`}>
                          {s.data_origin}
                        </span>
                      </div>
                      <div className="text-ink">
                        Method: <span className="text-ink font-medium">{s.imaging_method}</span> | Location: <span className="text-ink">{s.sampling_location}</span>
                      </div>
                      {s.reference_lab_method && (
                        <div className="text-muted">
                          Lab: {s.reference_lab_method} - <span className="text-confirmed">{s.reference_lab_result}</span>
                        </div>
                      )}
                      {s.image_path && (
                        <div className="mt-2">
                          <img
                            src={`/uploads/${s.image_safe_name}`}
                            alt={s.sample_id}
                            className="max-h-36 rounded border border-line"
                          />
                        </div>
                      )}
                      <div className="text-[10px] text-muted font-mono">
                        Collected: {s.collection_time}
                      </div>
                    </div>
                  ))}
                </div>
              )}
            </div>
          </div>
        )}

        {/* ---------------------------------------------------- */}
        {/* VIEW 4: AI ANALYSIS */}
        {/* ---------------------------------------------------- */}
        {activeTab === 'ai' && (
          <div className="space-y-6">
            <div className="flex items-center justify-between">
              <div>
                <h3 className="text-base font-bold text-ink">Experimental AI analysis</h3>
                <p className="text-xs text-muted">Analyse readings and the latest saved sample with a connected model service.</p>
              </div>

              <button
                onClick={handleRunInference}
                disabled={loading}
                className="px-4 py-2 bg-accent hover:bg-accent text-white text-xs font-semibold rounded-lg flex items-center gap-2"
              >
                <Zap className="w-3.5 h-3.5" />
                {loading ? 'Analysing…' : samples.length ? 'Analyse readings & latest sample' : 'Analyse readings'}
              </button>
            </div>

            {/* AI Service Status */}
            <div className="p-4 rounded-xl bg-surface border border-line flex items-center justify-between text-xs">
              <div className="flex items-center gap-3">
                <div className={`w-3 h-3 rounded-full ${status?.ai_health.available ? 'bg-emerald-400' : 'bg-slate-500'}`}></div>
                <div>
                  <span className="font-semibold text-ink">Local model service</span>
                </div>
              </div>
              <span className={`px-2.5 py-1 rounded font-medium ${
                status?.ai_health.available ? 'bg-confirmed-soft text-confirmed border border-line' : 'bg-subtle text-muted'
              }`}>
                {status?.ai_health.available ? `Connected (${status.ai_health.service_status})` : 'AI Service Unavailable'}
              </span>
            </div>

            {/* 3 Model Cards */}
            <div className="grid grid-cols-1 md:grid-cols-3 gap-6">
              {/* Sensor Score */}
              <div className="p-5 rounded-xl bg-surface border border-line space-y-3">
                <div className="flex items-center justify-between">
                  <span className="text-xs text-muted font-semibold uppercase">Sensor Risk Score</span>
                  <Waves className="w-4 h-4 text-accent" />
                </div>
                <div className="text-3xl font-mono font-bold text-ink">
                  {status?.predictions.sensors?.risk_score !== null && status?.predictions.sensors?.risk_score !== undefined
                    ? `${status.predictions.sensors.risk_score} / 100`
                    : 'Unavailable'}
                </div>
                <div className="text-xs text-muted space-y-1">
                  <div>Model: {status?.predictions.sensors?.model_name || 'Not supplied'}</div>
                  <div>Status: <span className="font-mono text-accent">{status?.predictions.sensors?.validation_status || 'NOT RUN'}</span></div>
                </div>
              </div>

              {/* Vision Score */}
              <div className="p-5 rounded-xl bg-surface border border-line space-y-3">
                <div className="flex items-center justify-between">
                  <span className="text-xs text-muted font-semibold uppercase">Vision Risk Score</span>
                  <Microscope className="w-4 h-4 text-accent" />
                </div>
                <div className="text-3xl font-mono font-bold text-ink">
                  {status?.predictions.vision?.risk_score !== null && status?.predictions.vision?.risk_score !== undefined
                    ? `${status.predictions.vision.risk_score} / 100`
                    : 'Unavailable'}
                </div>
                <div className="text-xs text-muted space-y-1">
                  <div>Model: {status?.predictions.vision?.model_name || 'Not supplied'}</div>
                  <div>Status: <span className="font-mono text-accent">{status?.predictions.vision?.validation_status || 'NO SAMPLES'}</span></div>
                </div>
              </div>

              {/* Composite Score */}
              <div className="p-5 rounded-xl bg-surface border border-line space-y-3">
                <div className="flex items-center justify-between">
                  <span className="text-xs text-muted font-semibold uppercase">Composite Indicator</span>
                  <Layers className="w-4 h-4 text-warning" />
                </div>
                <div className="text-3xl font-mono font-bold text-ink">
                  {status?.predictions.composite?.risk_score !== null && status?.predictions.composite?.risk_score !== undefined
                    ? `${status.predictions.composite.risk_score} / 100`
                    : 'Unavailable'}
                </div>
                <div className="text-xs text-muted space-y-1">
                  <div>Label: <span className="font-medium text-warning">Experimental composite indicator</span></div>
                  <div className="text-[11px] text-muted">
                    Never interpreted as a validated biological probability of contamination.
                  </div>
                </div>
              </div>
            </div>

            {/* Prototype Rules & Safety Constraints Notice */}
            <div className="p-4 rounded-xl bg-surface border border-line text-xs space-y-2 text-muted">
              <h4 className="font-semibold text-ink flex items-center gap-2">
                <Shield className="w-4 h-4 text-accent" />
                Prototype Safety Constraints
              </h4>
              <ul className="list-disc list-inside space-y-1 text-muted">
                <li>Composite scoring is disabled by default and requires two valid, compatible inputs.</li>
                <li>Simulated (<code className="text-warning font-mono">SIMULATED</code>) or reference (<code className="text-accent font-mono">REFERENCE</code>) data is prohibited from triggering hardware shutdown.</li>
                <li>Falling AI risk scores NEVER unlatch a manual stop request.</li>
              </ul>
            </div>
          </div>
        )}

        {/* ---------------------------------------------------- */}
        {/* VIEW 5: EVENTS & CONTROLS */}
        {/* ---------------------------------------------------- */}
        {activeTab === 'controls' && (
          <div className="space-y-6">
            <div className="grid grid-cols-1 lg:grid-cols-2 gap-6">
              {/* Command Tracker */}
              <div className="p-5 rounded-xl bg-surface border border-line space-y-4">
                <h3 className="text-sm font-bold text-ink flex items-center gap-2 border-b border-line pb-3">
                  <Power className="w-4 h-4 text-danger" />
                  Active Command Tracker
                </h3>
                {safety?.command_details ? (
                  <div className="space-y-2 text-xs font-mono">
                    <div className="flex justify-between py-1 border-b border-line">
                      <span className="text-muted">Command ID:</span>
                      <span className="text-accent font-bold">{safety.command_details.command_id}</span>
                    </div>
                    <div className="flex justify-between py-1 border-b border-line">
                      <span className="text-muted">Command Type:</span>
                      <span className="text-ink">{safety.command_details.command_type}</span>
                    </div>
                    <div className="flex justify-between py-1 border-b border-line">
                      <span className="text-muted">Origin:</span>
                      <span className="text-warning">{safety.command_details.origin}</span>
                    </div>
                    <div className="flex justify-between py-1 border-b border-line">
                      <span className="text-muted">Status:</span>
                      <span className={`${/FAILED|TIMEOUT|EXPIRED/.test(safety.command_status) ? 'text-danger' : 'text-accent'} font-bold`}>{safety.command_status}</span>
                    </div>
                    <div className="flex justify-between py-1 border-b border-line">
                      <span className="text-muted">Created:</span>
                      <span className="text-ink">{safety.command_details.created_at}</span>
                    </div>
                    <div className="flex justify-between py-1">
                      <span className="text-muted">Expires:</span>
                      <span className="text-ink">{safety.command_details.expires_at}</span>
                    </div>
                  </div>
                ) : (
                  <div className="text-xs text-muted p-4 text-center">
                    No active hardware commands dispatched for this node.
                  </div>
                )}
              </div>

              {/* Latch Status */}
              <div className="p-5 rounded-xl bg-surface border border-line space-y-4">
                <h3 className="text-sm font-bold text-ink flex items-center gap-2 border-b border-line pb-3">
                  <Shield className="w-4 h-4 text-confirmed" />
                  Manual Stop Latch Safety Status
                </h3>
                {isLatched ? (
                  <div className="p-4 rounded bg-danger-soft border border-danger-soft text-xs text-danger space-y-2">
                    <div className="font-bold text-danger">🛑 MANUAL STOP LATCH ACTIVE</div>
                    <p>Reason: {safety?.stop_reason}</p>
                    <p className="text-[11px] text-danger">
                      The platform stop latch is active. Check device feedback for reported equipment state.
                      Clearing alarms or receiving low AI scores will NOT clear this latch.
                    </p>
                  </div>
                ) : (
                  <div className="p-4 rounded bg-confirmed-soft border border-line text-xs text-confirmed space-y-1">
                    <div className="font-bold text-ink">NO MANUAL STOP LATCH</div>
                    <p className="text-muted">No operator emergency stop latched.</p>
                  </div>
                )}
              </div>
            </div>

            {/* Audit Log Table */}
            <div className="p-5 rounded-xl bg-surface border border-line space-y-4">
              <h3 className="text-sm font-bold text-ink flex items-center gap-2 border-b border-line pb-3">
                <FileText className="w-4 h-4 text-accent" />
                Activity log
              </h3>
              <div className="max-h-80 overflow-y-auto">
                <table className="w-full text-xs text-left">
                  <thead className="bg-subtle text-muted font-mono uppercase">
                    <tr>
                      <th className="p-2.5">Timestamp</th>
                      <th className="p-2.5">Severity</th>
                      <th className="p-2.5">Event Type</th>
                      <th className="p-2.5">Message</th>
                    </tr>
                  </thead>
                  <tbody className="divide-y divide-line font-mono">
                    {events.map((ev) => (
                      <tr key={ev.id} className="hover:bg-subtle">
                        <td className="p-2.5 text-muted">{ev.timestamp}</td>
                        <td className="p-2.5">
                          <span className={`px-2 py-0.5 rounded text-[10px] uppercase font-bold ${
                            ev.severity === 'CRITICAL' ? 'bg-danger-soft text-danger border border-danger-soft' :
                            ev.severity === 'WARNING' ? 'bg-warning-soft text-warning border border-warning-soft' :
                            'bg-subtle text-ink border border-line'
                          }`}>
                            {ev.severity}
                          </span>
                        </td>
                        <td className="p-2.5 text-ink">{ev.event_type}</td>
                        <td className="p-2.5 text-ink">{ev.message}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            </div>

          </div>
        )}

        {/* ---------------------------------------------------- */}
        {/* VIEW 6: VERIFICATION & TESTS */}
        {/* ---------------------------------------------------- */}
        {activeTab === 'verification' && (
          <div className="space-y-6">
            <div className="surface p-4"><WaterLevelSettings key={deviceId} deviceId={deviceId} calibration={status?.calibration} onSaved={fetchStatus} showSimulatorTools /></div>
            {deviceId === 'MISTSAFE-SIM-01' && <div className="surface p-4"><button className="button secondary" disabled={loading} onClick={handleRunSimulator}>Generate 5 test readings</button><p className="muted mt-2">Explicit test packets for the simulator only. Periodic readings are controlled separately.</p></div>}
            <div className="flex items-center justify-between">
              <div>
                <h3 className="text-base font-bold text-ink">Automated Verification Suite</h3>
                <p className="text-xs text-muted">Run software checks. Review the actual execution output below.</p>
              </div>

              <button
                onClick={handleRunTests}
                disabled={testRunning}
                className="px-4 py-2 bg-confirmed hover:bg-confirmed text-white text-xs font-semibold rounded-lg flex items-center gap-2"
              >
                <RefreshCw className={`w-3.5 h-3.5 ${testRunning ? 'animate-spin' : ''}`} />
                {testRunning ? 'Running Tests...' : 'Execute Pytest Suite'}
              </button>
            </div>

            <details className="surface p-4"><summary>Reset event contract & simulator test</summary>
            {/* Local Reset Specification & Test Reconciliation */}
            <div className="p-5 rounded-xl bg-surface border border-line space-y-4">
              <div className="border-b border-line pb-3">
                <h3 className="text-sm font-bold text-ink flex items-center gap-2">
                  <Shield className="w-4 h-4 text-confirmed" />
                  Physical Hardware Local-Reset Event Specification
                </h3>
                <p className="text-xs text-muted mt-0.5">
                  Contract: <code className="text-accent">mistsafe/{'{device_id}'}/events</code> (QoS 1). Retained messages rejected.
                </p>
              </div>

              <div className="grid grid-cols-1 lg:grid-cols-2 gap-4">
                {/* Contract JSON Spec */}
                <div className="space-y-2">
                  <span className="text-xs font-semibold text-ink">Contract Payload Specification:</span>
                  <pre className="p-3 bg-black/60 rounded border border-line font-mono text-xs text-accent overflow-x-auto">
{`{
  "event_type": "LOCAL_RESET",
  "event_id": "rst-evt-esp32-00412",
  "device_id": "${deviceId}",
  "boot_id": "boot-88ef3",
  "seq": 1,
  "timestamp": "${new Date().toISOString()}",
  "stop_command_id": "${status?.safety?.command_details?.command_id || 'cmd-8f3b20c19a4e'}",
  "stop_generation": ${status?.safety?.stop_generation || 1},
  "outcome": "SUCCESS",
  "remaining_faults": []
}`}
                  </pre>
                  <p className="text-[11px] text-muted">
                    The platform clears the stop latch only after matching stop generation, strictly ordered sequence, outcome SUCCESS, zero faults, and fresh telemetry confirming interlock NORMAL, pump OFF, valve CLOSED.
                  </p>
                </div>

                {/* Interactive Simulator for Testing Local Reset */}
                {deviceId === 'MISTSAFE-SIM-01' ? <form onSubmit={handleProcessLocalReset} className="space-y-3 bg-subtle p-4 rounded-lg border border-line text-xs">
                  <span className="font-semibold text-ink block">Test simulator reset-event intake</span>
                  <div className="grid grid-cols-2 gap-2">
                    <div>
                      <label htmlFor="field-7" className="text-muted block mb-0.5">Event ID</label>
                      <input id="field-7"
                        type="text"
                        value={rstEventId}
                        onChange={(e) => setRstEventId(e.target.value)}
                        className="w-full bg-surface border border-line rounded p-1.5 font-mono text-ink"
                      />
                    </div>
                    <div>
                      <label htmlFor="field-8" className="text-muted block mb-0.5">Boot / Session ID</label>
                      <input id="field-8"
                        type="text"
                        value={rstBootId}
                        onChange={(e) => setRstBootId(e.target.value)}
                        className="w-full bg-surface border border-line rounded p-1.5 font-mono text-ink"
                      />
                    </div>
                  </div>

                  <div className="grid grid-cols-2 gap-2">
                    <div>
                      <label htmlFor="field-9" className="text-muted block mb-0.5">Sequence (seq)</label>
                      <input id="field-9"
                        type="number"
                        value={rstSeq}
                        onChange={(e) => setRstSeq(parseInt(e.target.value) || 1)}
                        className="w-full bg-surface border border-line rounded p-1.5 font-mono text-ink"
                      />
                    </div>
                    <div>
                      <label htmlFor="field-10" className="text-muted block mb-0.5">Reported Outcome</label>
                      <select id="field-10"
                        value={rstOutcome}
                        onChange={(e) => setRstOutcome(e.target.value as any)}
                        className="w-full bg-surface border border-line rounded p-1.5 text-ink"
                      >
                        <option value="SUCCESS">SUCCESS (0 Faults)</option>
                        <option value="FAILED">FAILED (Active Faults)</option>
                      </select>
                    </div>
                  </div>

                  <div>
                    <label htmlFor="field-11" className="text-muted block mb-0.5">Remaining Faults (comma-separated, empty for none)</label>
                    <input id="field-11"
                      type="text"
                      placeholder="e.g. E01_FLOW_LOW"
                      value={rstFaults}
                      onChange={(e) => setRstFaults(e.target.value)}
                      className="w-full bg-surface border border-line rounded p-1.5 font-mono text-ink"
                    />
                  </div>

                  <button
                    type="submit"
                    disabled={loading}
                    className="w-full py-2 bg-confirmed hover:bg-confirmed text-white font-semibold rounded transition"
                  >
                    Submit Local-Reset Event
                  </button>
                </form> : <p className="text-xs text-muted">Real-device reset confirmations must come from the ESP32 through MQTT. Browser test submissions are disabled.</p>}
              </div>

              {/* Reset Events History Table */}
              <div className="pt-2">
                <span className="text-xs font-semibold text-ink block mb-2">Recorded Reset Transitions (Audit Table):</span>
                {resetEvents.length === 0 ? (
                  <div className="p-3 bg-surface rounded text-muted text-xs">No local reset events recorded for this device.</div>
                ) : (
                  <div className="max-h-48 overflow-y-auto">
                    <table className="w-full text-xs text-left">
                      <thead className="bg-subtle text-muted font-mono uppercase">
                        <tr>
                          <th className="p-2">Received</th>
                          <th className="p-2">Event ID</th>
                          <th className="p-2">Seq</th>
                          <th className="p-2">Gen</th>
                          <th className="p-2">Outcome</th>
                          <th className="p-2">Status</th>
                        </tr>
                      </thead>
                      <tbody className="divide-y divide-line font-mono">
                        {resetEvents.map((re) => (
                          <tr key={re.id} className="hover:bg-subtle">
                            <td className="p-2 text-muted">{re.received_at}</td>
                            <td className="p-2 text-accent">{re.event_id}</td>
                            <td className="p-2">{re.seq}</td>
                            <td className="p-2">{re.stop_generation}</td>
                            <td className="p-2">
                              <span className={re.outcome === 'SUCCESS' ? 'text-confirmed font-bold' : 'text-danger'}>
                                {re.outcome}
                              </span>
                            </td>
                            <td className="p-2">
                              <span className={`px-1.5 py-0.5 rounded text-[10px] font-bold ${
                                re.processed_status === 'CONFIRMED' ? 'bg-confirmed-soft text-confirmed border border-line' :
                                'bg-danger-soft text-danger border border-danger-soft'
                              }`}>
                                {re.processed_status}
                              </span>
                            </td>
                          </tr>
                        ))}
                      </tbody>
                    </table>
                  </div>
                )}
              </div>
            </div>
            </details>
            <details className="surface p-4"><summary>What the software checks cover</summary>
            {/* Checklist of intended checks */}
            <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 gap-2.5 text-xs">
              {[
                '1. Empty startup remains unavailable / unknown',
                '2. Valid telemetry is saved and displayed',
                '3. Malformed messages do not crash ingestion',
                '4. Duplicate and out-of-order messages handled',
                '5. Stale data detection after freshness timeout',
                '6. Multi-session shared state via SQLite WAL',
                '7. AI unavailability does not break monitoring',
                '8. Missing inference results do not become zero',
                '9. Shutdown requests persist and deduplicated',
                '10. Command acks matched strictly by command_id',
                '11. Simulated inputs cannot command hardware',
                '12. Manual stop cannot be cleared by falling AI scores',
                '13. Simulator reset leaves equipment stopped (pump OFF, valve CLOSED)',
                '14. Simulated start requires a separate explicit action',
                '15. Active faults block both reset and start operations',
                '16. Test controls cannot target real production hardware',
                '17. Unconfirmed local reset does not clear platform latch',
                '18. Old reset event cannot clear newer stop generation',
                '19. New stop takes precedence over in-progress reset',
                '20. Successfully reset stop generations cannot send delayed commands',
                '21. Latch changes persist across browser sessions via WAL',
                '22. Dashboard reruns do not duplicate hardware commands'
              ].map((inv, idx) => (
                <div key={idx} className="p-2.5 bg-surface border border-line rounded-lg flex items-center gap-2">
                  <Info className="w-3.5 h-3.5 text-muted shrink-0" />
                  <span className="text-ink text-[11px]">{inv}</span>
                </div>
              ))}
            </div>

            </details>
            <details className="surface p-4"><summary>Latest raw device status</summary><pre className="text-xs overflow-x-auto p-3">{JSON.stringify(status?.state || {}, null, 2)}</pre></details>
            {/* Test Output Console */}
            <div className="p-4 rounded-xl bg-surface border border-line space-y-2">
              <div className="flex items-center justify-between text-xs text-muted border-b border-line pb-2">
                <span className="font-mono">pytest execution output:</span>
                <span className="text-ink font-semibold font-mono">{testRunning ? 'Running' : testResult === 'passed' ? 'Passed · latest run' : testResult === 'failed' ? 'Failed · review output' : 'Not run'}</span>
              </div>
              <pre className="p-3 bg-black/60 rounded font-mono text-xs text-confirmed whitespace-pre-wrap overflow-x-auto max-h-72">
                {testOutput || 'Click "Execute Pytest Suite" to run software checks.'}
              </pre>
            </div>
          </div>
        )}

      </main>
    </div>
  );
}
