'use client';

import { useCallback, useEffect, useRef, useState } from 'react';
import { DONE, RACING, STAGING, RaceBar, badgeTone, statusText } from '../../race-clock';

type View = {
  ok: true;
  room: string;
  event: string;
  mode: 'manual' | 'semi';
  raceStatus: number;
  raceElapsed: number;
  raceLimit: number;
  timerOnline: boolean;
  timerAge: number;
  seq: number;
  seat: number;
  label: string;
  callsign: string | null;
  laps: number;
  lastLap: string | null;
  bestLap: string | null;
};

type Press = { button: 'add' | 'del'; at: number };

const POLL_RACING = 1200;
const POLL_IDLE = 4000;
const POLL_HIDDEN = 15000;
const PENDING_TIMEOUT = 8000;
const MAX_BATCH = 20; // must not exceed the relay's own per-request cap

export default function JudgeClient({ token }: { token: string }) {
  const [view, setView] = useState<View | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [outbox, setOutbox] = useState<Press[]>([]);
  const [flash, setFlash] = useState<'add' | 'del' | null>(null);

  // Presses are held here until the timer's own lap count moves, so the judge
  // always sees whether the tap has landed.
  const outboxRef = useRef<Press[]>([]);
  const sendingRef = useRef(false);
  const lapsAtPress = useRef<number | null>(null);
  const pendingSince = useRef(0);
  const lastPointer = useRef(0);

  const refresh = useCallback(async () => {
    try {
      const res = await fetch(`/api/judge/${token}`, { cache: 'no-store' });
      if (res.status === 404) {
        setError('This judge link is not active. Ask the race director for a new one.');
        return;
      }
      if (!res.ok) throw new Error(String(res.status));
      applyView(await res.json());
      setError(null);
    } catch {
      setError('offline');
    }
  }, [token]);

  const applyView = useCallback((next: View) => {
    setView(next);
    const moved = lapsAtPress.current !== null && next.laps !== lapsAtPress.current;
    const expired = pendingSince.current > 0 && Date.now() - pendingSince.current > PENDING_TIMEOUT;
    if (moved || expired) {
      lapsAtPress.current = null;
      pendingSince.current = 0;
    }
  }, []);

  /** Drain the outbox. Whatever does not get through stays queued, so a lost
   *  signal delays a tap instead of losing it. */
  const flush = useCallback(async () => {
    if (sendingRef.current || outboxRef.current.length === 0) return;
    sendingRef.current = true;
    try {
      while (outboxRef.current.length > 0) {
        // The relay accepts a bounded batch, so a long offline stretch goes as
        // several requests rather than being silently truncated.
        const batch = outboxRef.current.slice(0, MAX_BATCH);
        const now = performance.now();
        const res = await fetch(`/api/judge/${token}/press`, {
          method: 'POST',
          headers: { 'content-type': 'application/json' },
          body: JSON.stringify({
            button: batch[0].button,
            presses: batch.map((p) => ({ button: p.button, ageAtSend: Math.round(now - p.at) })),
          }),
          keepalive: true,
        });
        if (!res.ok) throw new Error(String(res.status));
        outboxRef.current = outboxRef.current.slice(batch.length);
        setOutbox(outboxRef.current);
        const body = await res.json();
        if (body?.ok && body.room) applyView(body as View);
        setError(null);
      }
    } catch {
      setError('offline');
    } finally {
      sendingRef.current = false;
    }
  }, [token, applyView]);

  const press = useCallback(
    (button: 'add' | 'del') => {
      // The tap time is captured here, before any network work, and travels
      // with the command - relay latency never moves the lap time.
      const entry: Press = { button, at: performance.now() };
      outboxRef.current = [...outboxRef.current, entry];
      setOutbox(outboxRef.current);
      if (lapsAtPress.current === null && view) {
        lapsAtPress.current = view.laps;
        pendingSince.current = Date.now();
      }
      setFlash(button);
      window.setTimeout(() => setFlash(null), 220);
      navigator.vibrate?.(button === 'add' ? 25 : [15, 40, 15]);
      void flush();
    },
    [flush, view],
  );

  // Pointer-down is what a judge feels as instant. Click is the safety net for
  // anything that never sends pointer events (a desktop marshal, an assistive
  // device); it is ignored when the pointer path already fired.
  const onPointer = useCallback(
    (button: 'add' | 'del') => {
      lastPointer.current = Date.now();
      press(button);
    },
    [press],
  );

  const onClick = useCallback(
    (button: 'add' | 'del') => {
      if (Date.now() - lastPointer.current < 700) return;
      press(button);
    },
    [press],
  );

  /* ---- polling, paced by what is actually happening ---- */
  useEffect(() => {
    let timer = 0;
    let stopped = false;
    const tick = async () => {
      if (stopped) return;
      await refresh();
      void flush();
      const hidden = document.visibilityState === 'hidden';
      // Staging is short: poll it at racing speed so the clock turns green
      // the moment the race actually starts.
      const live = view?.raceStatus === RACING || view?.raceStatus === STAGING;
      const delay = hidden ? POLL_HIDDEN : live ? POLL_RACING : POLL_IDLE;
      timer = window.setTimeout(tick, delay);
    };
    void tick();
    const wake = () => {
      window.clearTimeout(timer);
      void tick();
    };
    document.addEventListener('visibilitychange', wake);
    window.addEventListener('online', wake);
    return () => {
      stopped = true;
      window.clearTimeout(timer);
      document.removeEventListener('visibilitychange', wake);
      window.removeEventListener('online', wake);
    };
  }, [refresh, flush, view?.raceStatus]);

  /* ---- keep the screen alive while judging ---- */
  useEffect(() => {
    let lock: WakeLockSentinel | null = null;
    const acquire = async () => {
      try {
        lock = (await navigator.wakeLock?.request('screen')) ?? null;
      } catch {
        /* denied or unsupported - the judge just keeps tapping */
      }
    };
    void acquire();
    const reacquire = () => {
      if (document.visibilityState === 'visible') void acquire();
    };
    document.addEventListener('visibilitychange', reacquire);
    return () => {
      document.removeEventListener('visibilitychange', reacquire);
      void lock?.release().catch(() => {});
    };
  }, []);

  if (error && error !== 'offline' && !view) {
    return (
      <main className="page">
        <h1>Link not active</h1>
        <p className="lede">{error}</p>
      </main>
    );
  }

  if (!view) {
    return (
      <main className="page">
        <h1>
          Connecting<span className="gradient-text">…</span>
        </h1>
        <p className="lede">Loading your pilot.</p>
      </main>
    );
  }

  const offline = error === 'offline';
  const queued = outbox.length;
  const racing = view.raceStatus === RACING;
  const canDelete = racing || view.raceStatus === DONE;

  return (
    <main className="judge">
      <header className="judge-head">
        <span className="seat-mark">{view.label || `S${view.seat + 1}`}</span>
        <div className="judge-pilot">
          <div className="judge-callsign">{view.callsign ?? `Seat ${view.seat + 1}`}</div>
          <div className="judge-sub">
            {view.event || 'No heat selected'} · room {view.room}
          </div>
        </div>
      </header>

      <RaceBar
        status={view.raceStatus}
        elapsed={view.raceElapsed}
        limit={view.raceLimit}
        mode={view.mode}
        label={statusLabel(offline, view)}
        tone={statusClass(offline, view)}
      />

      <section className="judge-body">
        <div className="card count-card">
          <div className="stat-label">Gate passes</div>
          <div className="lap-count" aria-live="polite">
            <span className="gradient-text">{view.laps}</span>
            {queued > 0 && <span className="pending">+{queued} sending</span>}
          </div>
          <div className="lap-times">
            <div>
              <div className="stat-label">Last lap</div>
              <div className="lap-time-value">{view.lastLap ?? '—'}</div>
            </div>
            <div>
              <div className="stat-label">Best lap</div>
              <div className="lap-time-value">{view.bestLap ?? '—'}</div>
            </div>
          </div>
        </div>
        <p className={`judge-note ${racing ? '' : 'judge-note-warn'}`}>{hint(view, offline)}</p>
      </section>

      <div className="judge-actions">
        <button
          type="button"
          className={`action action-add ${flash === 'add' ? 'action-flash' : ''}`}
          onPointerDown={() => onPointer('add')}
          onClick={() => onClick('add')}
          disabled={!racing}
        >
          <span className="action-icon">
            <CheckIcon />
          </span>
          <span className="action-text">
            <span className="action-title">Add Lap</span>
            <span className="action-hint">at the gate</span>
          </span>
        </button>
        <button
          type="button"
          className={`action action-del ${flash === 'del' ? 'action-flash' : ''}`}
          onPointerDown={() => onPointer('del')}
          onClick={() => onClick('del')}
          disabled={!canDelete || view.laps === 0}
        >
          <span className="action-icon">
            <MinusIcon />
          </span>
          <span className="action-text">
            <span className="action-title">Remove Lap</span>
            <span className="action-hint">undo the last</span>
          </span>
        </button>
      </div>
    </main>
  );
}

function CheckIcon() {
  return (
    <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" aria-hidden="true">
      <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M5 13l4 4L19 7" />
    </svg>
  );
}

function MinusIcon() {
  return (
    <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" aria-hidden="true">
      <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M5 12h14" />
    </svg>
  );
}

function statusClass(offline: boolean, view: View): '' | 'badge-live' | 'badge-warn' | 'badge-down' {
  if (offline) return 'badge-down';
  if (!view.timerOnline) return 'badge-warn';
  return badgeTone(view.raceStatus);
}

function statusLabel(offline: boolean, view: View) {
  if (offline) return 'No signal';
  if (!view.timerOnline) return 'Timer offline';
  return statusText(view.raceStatus);
}

function hint(view: View, offline: boolean) {
  if (offline) return 'Taps are saved on this phone and sent when signal returns.';
  if (!view.timerOnline) return `Timer last seen ${view.timerAge}s ago.`;
  if (view.raceStatus === RACING) {
    return view.mode === 'manual'
      ? 'Tap Add Lap as your pilot crosses the gate.'
      : 'Tap Add Lap at the gate to confirm the timer, or to add a missed lap.';
  }
  if (view.raceStatus === DONE) return 'Race finished. You can still remove a wrong lap.';
  return 'Waiting for the race to start.';
}
