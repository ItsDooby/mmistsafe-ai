import express from 'express';
import { createServer as createViteServer } from 'vite';
import { execFile, execFileSync } from 'child_process';
import 'dotenv/config';
import path from 'path';
import fs from 'fs';

const localPython = path.resolve('.venv', process.platform === 'win32' ? 'Scripts/python.exe' : 'bin/python');
const pythonExecutable = process.env.PYTHON_EXECUTABLE || (fs.existsSync(localPython) ? localPython : process.platform === 'win32' ? 'python' : 'python3');
function ensurePythonDependencies() {
  execFileSync(pythonExecutable, ['-c', 'import requests, PIL'], { stdio: 'inherit' });
}

const app = express();
const PORT = process.env.PORT ? parseInt(process.env.PORT) : 3000;

app.use(express.json({ limit: '15mb' }));

// Helper to run python bridge
function runPythonBridge(args: string[]): Promise<any> {
  return new Promise((resolve, reject) => {
    execFile(pythonExecutable, ['mistsafe/bridge.py', ...args], { cwd: process.cwd(), maxBuffer: 16 * 1024 * 1024 }, (err, stdout, stderr) => {
      if (err) {
        return reject(new Error(stderr || err.message));
      }
      try {
        const parsed = JSON.parse(stdout.trim());
        resolve(parsed);
      } catch (parseErr: any) {
        reject(new Error('Invalid JSON response from Python bridge'));
      }
    });
  });
}

// REST API Endpoints
for (const [route, action] of [['/api/calibration', 'calibration'], ['/api/simulator/water-level', 'water_level']]) {
  app.post(route, async (req, res) => {
    try {
      const data = await runPythonBridge([action, req.body.device_id || 'MISTSAFE-ESP32-01', action === 'calibration' ? JSON.stringify(req.body) : String(req.body.mode)]);
      res.status(data.error ? 400 : 200).json(data);
    } catch (err: any) {
      res.status(500).json({ error: err.message });
    }
  });
}
app.get('/api/status', async (req, res) => {
  const deviceId = (req.query.device_id as string) || 'MISTSAFE-ESP32-01';
  try {
    const data = await runPythonBridge(['status', deviceId]);
    res.json(data);
  } catch (err: any) {
    res.status(500).json({ error: err.message });
  }
});

app.get('/api/history', async (req, res) => {
  const deviceId = (req.query.device_id as string) || 'MISTSAFE-ESP32-01';
  const hours = (req.query.hours as string) || 'all';
  try {
    const data = await runPythonBridge(['history', deviceId, hours]);
    res.json(data);
  } catch (err: any) {
    res.status(500).json({ error: err.message });
  }
});

app.get('/api/events', async (req, res) => {
  const deviceId = (req.query.device_id as string) || 'all';
  const severity = (req.query.severity as string) || 'ALL';
  try {
    const data = await runPythonBridge(['events', deviceId, severity]);
    res.json(data);
  } catch (err: any) {
    res.status(500).json({ error: err.message });
  }
});

app.get('/api/reset-events', async (req, res) => {
  const deviceId = (req.query.device_id as string) || 'all';
  try {
    const data = await runPythonBridge(['reset_events', deviceId]);
    res.json(data);
  } catch (err: any) {
    res.status(500).json({ error: err.message });
  }
});

app.get('/api/samples', async (req, res) => {
  const deviceId = (req.query.device_id as string) || 'all';
  try {
    const data = await runPythonBridge(['samples', deviceId]);
    res.json(data);
  } catch (err: any) {
    res.status(500).json({ error: err.message });
  }
});

app.post('/api/shutdown', async (req, res) => {
  const deviceId = req.body.device_id || 'MISTSAFE-ESP32-01';
  const reason = req.body.reason || 'Operator manual emergency stop requested';
  try {
    const data = await runPythonBridge(['shutdown', deviceId, reason]);
    if (data.success === false) {
      res.status(400).json(data);
    } else {
      res.json(data);
    }
  } catch (err: any) {
    res.status(500).json({ success: false, error: err.message });
  }
});

app.post('/api/predict', async (req, res) => {
  const deviceId = req.body.device_id || 'MISTSAFE-ESP32-01';
  try {
    const data = await runPythonBridge(['predict', deviceId]);
    res.json(data);
  } catch (err: any) {
    res.status(500).json({ error: err.message });
  }
});

// Explicit simulation launcher for benchtop verification
app.post('/api/simulate', (req, res) => {
  const count = parseInt(req.body.count || '5');
  const deviceId = req.body.device_id || 'MISTSAFE-SIM-01';
  if (!Number.isInteger(count) || count < 1 || count > 100 || deviceId !== 'MISTSAFE-SIM-01') return res.status(400).json({error: 'Use simulator device and count 1–100'});
  execFile(pythonExecutable, ['simulator.py', '--count', String(count), '--interval', '0.5', '--device-id', deviceId], { cwd: process.cwd() }, (err, stdout, stderr) => {
    if (err) {
      return res.status(500).json({ error: stderr || err.message });
    }
    res.json({ success: true, count, device_id: deviceId, output: stdout });
  });
});

// Simulator Reset test device
app.post('/api/simulator/reset', async (req, res) => {
  const deviceId = req.body.device_id || 'MISTSAFE-SIM-01';
  try {
    const data = await runPythonBridge(['reset_simulator', deviceId]);
    res.json(data);
  } catch (err: any) {
    res.status(500).json({ error: err.message });
  }
});

// Simulator Start simulated operation
app.post('/api/simulator/start', async (req, res) => {
  const deviceId = req.body.device_id || 'MISTSAFE-SIM-01';
  try {
    const data = await runPythonBridge(['start_simulator', deviceId]);
    res.json(data);
  } catch (err: any) {
    res.status(500).json({ error: err.message });
  }
});

// Local reset event intake (from ESP32 or test runner)
app.post('/api/local-reset', async (req, res) => {
  const deviceId = req.body.device_id || 'MISTSAFE-ESP32-01';
  if (typeof deviceId !== 'string' || !deviceId.toUpperCase().startsWith('MISTSAFE-SIM-')) {
    return res.status(403).json({ success: false, error: 'Real-device reset confirmations must arrive through the device MQTT event path.' });
  }
  const eventPayload = req.body.event_payload || req.body;
  try {
    const data = await runPythonBridge(['local_reset', deviceId, JSON.stringify(eventPayload)]);
    res.json(data);
  } catch (err: any) {
    res.status(500).json({ error: err.message });
  }
});

// Simulator fault injection and clearing
app.post('/api/simulator/faults', async (req, res) => {
  const deviceId = req.body.device_id || 'MISTSAFE-SIM-01';
  const faults = (req.body.faults || []).join(',');
  try {
    const data = await runPythonBridge(['set_simulator_faults', deviceId, faults]);
    res.json(data);
  } catch (err: any) {
    res.status(500).json({ error: err.message });
  }
});

app.post('/api/simulator/inject-flow-loss', async (req, res) => {
  const deviceId = req.body.device_id || 'MISTSAFE-SIM-01';
  try {
    const data = await runPythonBridge(['inject_flow_loss', deviceId]);
    res.json(data);
  } catch (err: any) {
    res.status(500).json({ error: err.message });
  }
});

app.post('/api/simulator/inject-missing-sensor', async (req, res) => {
  const deviceId = req.body.device_id || 'MISTSAFE-SIM-01';
  const sensor = req.body.sensor || 'temp';
  try {
    const data = await runPythonBridge(['inject_missing_sensor', deviceId, sensor]);
    res.json(data);
  } catch (err: any) {
    res.status(500).json({ error: err.message });
  }
});

app.post('/api/simulator/inject-out-of-limit', async (req, res) => {
  const deviceId = req.body.device_id || 'MISTSAFE-SIM-01';
  const sensor = req.body.sensor || 'turbidity';
  const value = req.body.value !== undefined ? String(req.body.value) : '18.0';
  try {
    const data = await runPythonBridge(['inject_out_of_limit', deviceId, sensor, value]);
    res.json(data);
  } catch (err: any) {
    res.status(500).json({ error: err.message });
  }
});

app.post('/api/simulator/clear-faults', async (req, res) => {
  const deviceId = req.body.device_id || 'MISTSAFE-SIM-01';
  try {
    const data = await runPythonBridge(['clear_simulator_faults', deviceId]);
    res.json(data);
  } catch (err: any) {
    res.status(500).json({ error: err.message });
  }
});

app.get('/api/simulator/config', async (req, res) => {
  const deviceId = (req.query.device_id as string) || 'MISTSAFE-SIM-01';
  try {
    const data = await runPythonBridge(['get_simulator_config', deviceId]);
    res.json(data);
  } catch (err: any) {
    res.status(500).json({ error: err.message });
  }
});

app.post('/api/simulator/telemetry-control', async (req, res) => {
  const deviceId = req.body.device_id || 'MISTSAFE-SIM-01';
  const mode = req.body.mode || 'resume'; // pause or resume
  try {
    const data = await runPythonBridge(['simulator_telemetry_control', deviceId, mode]);
    res.json(data);
  } catch (err: any) {
    res.status(500).json({ error: err.message });
  }
});

// Explicit periodic ticker endpoint (also triggered automatically by server background timer)
app.post('/api/simulator/tick', async (req, res) => {
  const deviceId = req.body.device_id || 'MISTSAFE-SIM-01';
  try {
    const data = await runPythonBridge(['simulator_tick', deviceId]);
    res.json(data);
  } catch (err: any) {
    res.status(500).json({ error: err.message });
  }
});


// Run automated pytest suite and return results to UI
app.post('/api/run-tests', (req, res) => {
  const testRoot = path.resolve('.test-runs');
  fs.mkdirSync(testRoot, { recursive: true });
  const runDirectory = fs.mkdtempSync(path.join(testRoot, 'run-'));
  execFile(pythonExecutable, ['-m', 'pytest', 'tests', '-v', '--basetemp', path.join(runDirectory, 'data')], { cwd: process.cwd() }, (err, stdout, stderr) => {
    res.json({
      success: !err,
      output: [stdout, stderr].filter(Boolean).join('\n'),
      exitCode: err ? (err as any).code : 0
    });
  });
});

// Serve sample images from uploads directory
const uploadsDir = path.resolve(process.cwd(), 'uploads');
if (!fs.existsSync(uploadsDir)) {
  fs.mkdirSync(uploadsDir, { recursive: true });
}
app.use('/uploads', express.static(uploadsDir));

// Sample image upload endpoint
app.post('/api/upload-sample', async (req, res) => {
  const { sample_id, device_id, location, method, data_origin, image_base64, lab_method, lab_result } = req.body;
  if (!image_base64) {
    return res.status(400).json({ success: false, error: 'No file selected. Please select a PNG or JPEG microscopy image.' });
  }

  try {
    const matches = image_base64.match(/^data:image\/([A-Za-z-+\/]+);base64,(.+)$/);
    const base64Data = matches ? matches[2] : image_base64;
    const buffer = Buffer.from(base64Data, 'base64');

    if (!buffer || buffer.length === 0) {
      return res.status(400).json({ success: false, error: 'Uploaded file is empty (0 bytes)' });
    }

    const MAX_BYTES = 10 * 1024 * 1024; // 10MB
    if (buffer.length > MAX_BYTES) {
      return res.status(400).json({ success: false, error: 'File size exceeds maximum limit of 10MB' });
    }

    // Magic bytes content inspection
    const isPng = buffer.length >= 8 &&
      buffer[0] === 0x89 && buffer[1] === 0x50 && buffer[2] === 0x4e && buffer[3] === 0x47 &&
      buffer[4] === 0x0d && buffer[5] === 0x0a && buffer[6] === 0x1a && buffer[7] === 0x0a;
    const isJpeg = buffer.length >= 3 &&
      buffer[0] === 0xff && buffer[1] === 0xd8 && buffer[2] === 0xff;

    if (!isPng && !isJpeg) {
      return res.status(400).json({
        success: false,
        error: 'Invalid image content: file is not a valid PNG or JPEG image (magic bytes mismatch)'
      });
    }

    const ext = isPng ? '.png' : '.jpg';
    const sId = (sample_id || `SMP-${Date.now()}`).trim();
    const devId = (device_id || 'MISTSAFE-ESP32-01').trim();
    const safeName = `sample_${Date.now()}_${Math.random().toString(36).substring(2, 8)}${ext}`;
    const filePath = path.join(uploadsDir, safeName);

    fs.writeFileSync(filePath, buffer);

    // Call bridge.py save_sample
    const payload = JSON.stringify({
      sample_id: sId,
      device_id: devId,
      location: location || 'Nozzle Manifold',
      method: method || 'Brightfield 40x',
      data_origin: data_origin || 'REAL',
      image_path: filePath,
      image_safe_name: safeName,
      lab_method: lab_method || '',
      lab_result: lab_result || ''
    });

    const bridgeResult = await runPythonBridge(['save_sample', payload]);
    if (!bridgeResult || bridgeResult.success !== true) {
      // Clean up orphaned file on duplicate ID or DB error
      try { if (fs.existsSync(filePath)) fs.unlinkSync(filePath); } catch (_) {}
      return res.status(400).json({
        success: false,
        error: bridgeResult?.error || 'Failed to confirm sample storage'
      });
    }

    res.json({
      success: true,
      sample_id: sId,
      device_id: bridgeResult.device_id || devId,
      safe_name: safeName,
      path: filePath
    });
  } catch (e: any) {
    res.status(500).json({ success: false, error: e.message || 'Internal server error during upload' });
  }
});

async function startServer() {
  ensurePythonDependencies();
  const isDev = process.env.NODE_ENV !== 'production';

  if (isDev) {
    const vite = await createViteServer({
      server: { middlewareMode: true },
      appType: 'spa',
    });
    app.use(vite.middlewares);
  } else {
    app.use(express.static(path.resolve(process.cwd(), 'dist')));
    app.get('*', (req, res) => {
      res.sendFile(path.resolve(process.cwd(), 'dist', 'index.html'));
    });
  }

  app.listen(PORT, '0.0.0.0', () => {
    console.log(`MistSafe AI Full-Stack Platform listening on http://0.0.0.0:${PORT}`);

    // Singleton periodic simulated telemetry generator:
    // Emits 1 telemetry message every 5 seconds for MISTSAFE-SIM-01 strictly when enabled in config.
    // Strictly isolates simulator from primary real device (MISTSAFE-ESP32-01).
    setInterval(async () => {
      try {
        await runPythonBridge(['simulator_tick', 'MISTSAFE-SIM-01']);
      } catch (err: any) {
        // Silently catch background tick errors to avoid crashing server
      }
    }, 5000);
  });
}

startServer();

