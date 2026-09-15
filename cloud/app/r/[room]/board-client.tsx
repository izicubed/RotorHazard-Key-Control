'use client';

import { useEffect, useState } from 'react';

type Board = {
  ok: true;
  room: string;
  event: string;
  mode: 'manual' | 'semi';
  raceStatus: number;
  timerOnline: boolean;
  seats: { seat: number; label: string; callsign: string | null; laps: number; lastLap: string | null; bestLap: string | null }[];
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
        <h1>Room {room}</h1>
        <p className="lede">
          No RotorHazard server is using this room code. Turn on Cloud judging in the KEY CONTROL
          panel, then reload.
        </p>
      </main>
    );
  }

  if (!board) {
    return (
      <main className="page">
        <h1>Room {room}</h1>
        <p className="lede">Loading…</p>
      </main>
    );
  }

  return (
    <main className="page">
      <h1>Room {board.room}</h1>
      <p className="lede">
        {board.event || 'No heat selected'} · {board.mode === 'manual' ? 'Manual' : 'Semi'} mode ·{' '}
        {board.timerOnline ? statusText(board.raceStatus) : 'timer offline'}
      </p>
      <div className="card">
        {board.seats.length === 0 && <p className="muted">No occupied seats in this heat.</p>}
        {board.seats.map((s) => (
          <div className="seat-row" key={s.seat}>
            <span className="chip">{s.label}</span>
            <div className="grow">
              <div style={{ fontWeight: 600 }}>{s.callsign ?? `Seat ${s.seat + 1}`}</div>
              <div className="muted">
                last {s.lastLap ?? '—'} · best {s.bestLap ?? '—'}
              </div>
            </div>
            <span className="num">{s.laps}</span>
          </div>
        ))}
      </div>
      <p className="muted">
        Read-only. Judge links are handed out from the KEY CONTROL panel on the timer.
      </p>
    </main>
  );
}

function statusText(status: number) {
  return ['ready', 'racing', 'race over', 'stopped'][status] ?? 'unknown';
}
