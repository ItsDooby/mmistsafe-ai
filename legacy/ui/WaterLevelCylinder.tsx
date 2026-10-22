import React, { useId } from 'react';

export interface Calibration { empty_distance_cm: number; full_distance_cm: number }
interface Props {
  distance?: number | null;
  sensorStatus?: string;
  calibration?: Calibration | null;
  fresh: boolean;
  timestamp?: string | null;
}

export default function WaterLevelCylinder({ distance, sensorStatus, calibration, fresh, timestamp }: Props) {
  const id = useId().replace(/:/g, '');
  const invalid = distance != null && (!Number.isFinite(distance) || distance < 0);
  const unavailable = distance == null || invalid || (sensorStatus !== undefined && sensorStatus !== 'OK');
  const calibrated = calibration && Number.isFinite(calibration.empty_distance_cm) && Number.isFinite(calibration.full_distance_cm)
    && calibration.full_distance_cm >= 0 && calibration.empty_distance_cm > calibration.full_distance_cm;
  const percent = !unavailable && calibrated ? 100 * (calibration.empty_distance_cm - distance!) / (calibration.empty_distance_cm - calibration.full_distance_cm) : null;
  const outOfRange = percent !== null && (percent < 0 || percent > 100);
  const y = 184 - Math.max(0, Math.min(100, percent ?? 0)) * 1.48;
  const message = unavailable ? 'Level unavailable' : !calibrated ? 'Calibration required' : outOfRange ? 'Outside calibrated range' : 'Usable fill height';
  return <figure className="water-cylinder" aria-label={`Water level: ${percent === null ? message : `${percent.toFixed(0)} percent. ${message}`}${!fresh && timestamp ? '. Stale' : ''}`}>
    <svg viewBox="0 0 200 228" role="img" aria-label="Measured water level cylinder">
      <defs>
        <clipPath id={`${id}-clip`}><path d="M36 36 A64 15 0 0 1 164 36 L164 184 A64 15 0 0 1 36 184 Z" /></clipPath>
        <linearGradient id={`${id}-shade`}><stop stopColor="#7295ab" stopOpacity=".22"/><stop offset=".5" stopColor="#a6bdcc" stopOpacity=".03"/><stop offset="1" stopColor="#7295ab" stopOpacity=".25"/></linearGradient>
      </defs>
      <path d="M36 36 A64 15 0 0 1 164 36 L164 184 A64 15 0 0 1 36 184 Z" fill={`url(#${id}-shade)`} stroke="#8196a5" />
      {percent !== null && <g clipPath={`url(#${id}-clip)`}>
        <g className={fresh ? 'water-fill' : ''} style={{ transform: `translateY(${y}px)` }}>
          <rect x="36" y="0" width="128" height="200" fill="#9db8c7" />
          <ellipse cx="100" cy="0" rx="64" ry="15" fill="#c0d2dc" />
        </g>
      </g>}
      <ellipse cx="100" cy="36" rx="64" ry="15" fill="none" stroke="#8196a5" />
      <path d="M36 184 A64 15 0 0 0 164 184" fill="none" stroke="#8196a5" />
      <text x="100" y="108" textAnchor="middle" fill="#263e4b" fontSize={percent === null ? '12' : '28'} fontWeight="600">{percent === null ? '—' : `${percent.toFixed(0)}%`}</text>
      <text x="100" y="129" textAnchor="middle" fill="#344f60" fontSize="11">Water level</text>
    </svg>
    <figcaption className="text-xs text-muted text-center">
      <div>{message}</div>
      {distance != null && <div>Distance: {String(distance)} cm</div>}
      {!fresh && timestamp && <div className="text-warning">Stale · {new Date(timestamp).toLocaleString()}</div>}
    </figcaption>
  </figure>;
}
