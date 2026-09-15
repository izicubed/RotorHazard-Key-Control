'use client';

import { useEffect, useState } from 'react';
import { RaceBar, badgeTone, statusText } from '../../race-clock';

type Seat = {
  seat: number;
  label: string;
  callsign: string | null;
  laps: number;
  lastLap: string | null;
  bestLap: string | null;
};

type Board = {
  ok: true;
  room: string;
  event: string;
  mode: 'manual' | 'semi';
  raceStatus: number;
  raceElapsed: number;
  raceLimit: number;
  timerOnline: boolean;
  seats: Seat[];
};

export default function BoardClient({ room }: { room: string }) {
  const [board, setBoard] = useState<Board | null>(null);
  const [missing, setMissing] = useState(false);

  useEffect(() => {
    let stopped = false;
    let timer = 0;
    const tick = async () => {
      if (stopped) return;
      try {
        const res = await fetch(`/api/board/${room}`, { cache: 'no-store' });
        if (res.status === 404) setMissing(true);
        else if (res.ok) {
          setBoard(await res.json());
          setMissing(false);
        }
      } catch {
        /* keep the last board on screen and try again */
      }
      timer = window.setTimeout(tick, document.visibilityState === 'hidden' ? 15000 : 2000);
    };
    void tick();
    return () => {
      stopped = true;
      window.clearTimeout(timer);
    };
  }, [room]);

  if (missing) {
    return (
      <main className="page">
        <h1>
          Room <span className="gradient-text">{room}</span>
        </h1>
        <p className="lede">
          No RotorHazard server is using this room code. Turn on Cloud judges in the KEY CONTROL
          panel, then reload.
        </p>
      </main>
    );
  }

  if (!board) {
    return (
      <main className="page">
        <h1>
          Room <span className="gradient-text">{room}</span>
        </h1>
        <p className="lede">Loading…</p>
      </main>
    );
  }

  const badge: '' | 'badge-live' | 'badge-warn' | 'badge-down' = board.timerOnline
    ? badgeTone(board.raceStatus)
    : 'badge-down';

  return (
    <main className="page">
      <div className="board-head">
        <div className="grow">
          <h1>
            Room <span className="gradient-text">{board.room}</span>
          </h1>
          <p className="lede" style={{ marginBottom: 0 }}>
            {board.event || 'No heat selected'} · {board.mode === 'manual' ? 'Manual' : 'Semi'} mode
          </p>
        </div>
      </div>

      <RaceBar
        inline
        status={board.raceStatus}
        elapsed={board.raceElapsed}
        limit={board.raceLimit}
        mode={board.mode}
        label={board.timerOnline ? statusText(board.raceStatus) : 'Timer offline'}
        tone={badge}
      />

      <div className="card">
        {board.seats.length === 0 && (
          <p className="muted" style={{ margin: 0 }}>
            No occupied seats in this heat.
          </p>
        )}
        {board.seats.map((s) => (
          <div className="seat-row" key={s.seat}>
            <span className="seat-mark">{s.label || `S${s.seat + 1}`}</span>
            <div className="grow">
              <div className="name">{s.callsign ?? `Seat ${s.seat + 1}`}</div>
              <div className="sub">
                last {s.lastLap ?? '—'} · best {s.bestLap ?? '—'}
              </div>
            </div>
            <div>
              <div className="seat-count">{s.laps}</div>
              <div className="stat-label" style={{ textAlign: 'right' }}>
                passes
              </div>
            </div>
          </div>
        ))}
      </div>

      <p className="footnote">
        Read-only. Judge links are handed out from the KEY CONTROL panel on the timer.
      </p>
    </main>
  );
}
