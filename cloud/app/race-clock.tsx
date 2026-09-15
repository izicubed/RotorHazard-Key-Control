'use client';

import { useEffect, useRef, useState } from 'react';

export const READY = 0;
export const RACING = 1;
export const DONE = 2;
export const STAGING = 3;

/** The race clock, ticking locally between snapshots so it never stutters on a
 *  slow poll and never runs on after the race stops. */
export function useRaceClock(elapsed: number, limit: number, status: number): number {
  const base = useRef({ at: 0, ms: elapsed, running: false });
  const [ms, setMs] = useState(elapsed);

  useEffect(() => {
    base.current = { at: performance.now(), ms: elapsed, running: status === RACING };
    setMs(elapsed);
  }, [elapsed, status]);

  useEffect(() => {
    const id = window.setInterval(() => {
      const b = base.current;
      setMs(b.running ? b.ms + (performance.now() - b.at) : b.ms);
    }, 200);
    return () => window.clearInterval(id);
  }, []);

  // A format with a time limit counts down, like the timer's own display.
  return limit > 0 ? Math.max(0, limit * 1000 - ms) : ms;
}

export function formatClock(ms: number): string {
  const total = Math.floor(ms / 1000);
  const hours = Math.floor(total / 3600);
  const minutes = Math.floor((total % 3600) / 60);
  const seconds = total % 60;
  const mm = hours > 0 ? String(minutes).padStart(2, '0') : String(minutes);
  return `${hours > 0 ? `${hours}:` : ''}${mm}:${String(seconds).padStart(2, '0')}`;
}

/** The clock carries the race state as colour: amber on the line, green once
 *  the race is on, muted the rest of the time. */
export function clockTone(status: number): string {
  if (status === STAGING) return 'race-clock-staging';
  if (status === RACING) return 'race-clock-racing';
  return 'race-clock-idle';
}

/** Matching tone for the status pill beside it. */
export function badgeTone(status: number): '' | 'badge-live' | 'badge-warn' {
  if (status === STAGING) return 'badge-warn';
  if (status === RACING) return 'badge-live';
  return '';
}

export function statusText(status: number): string {
  if (status === RACING) return 'Racing';
  if (status === DONE) return 'Race over';
  if (status === STAGING) return 'Staging';
  return 'Ready';
}

type Props = {
  status: number;
  elapsed: number;
  limit: number;
  mode: 'manual' | 'semi';
  label: string;
  tone: '' | 'badge-live' | 'badge-warn' | 'badge-down';
  inline?: boolean;
};

export function RaceBar({ status, elapsed, limit, mode, label, tone, inline }: Props) {
  const clock = useRaceClock(elapsed, limit, status);
  return (
    <div className={`race-bar ${inline ? 'race-bar-inline' : ''}`}>
      <span className={`badge ${tone}`}>
        <i className="dot" aria-hidden="true" />
        {label}
      </span>
      <div className={`race-clock ${clockTone(status)}`} role="timer" aria-live="off">
        {formatClock(clock)}
      </div>
      <span className="race-mode">{limit > 0 ? 'remaining' : mode === 'manual' ? 'Manual' : 'Semi'}</span>
    </div>
  );
}
