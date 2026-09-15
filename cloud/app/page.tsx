import Link from 'next/link';
import { usingDatabase } from '@/lib/store';

export const dynamic = 'force-dynamic';

export default function Home() {
  return (
    <main className="page">
      <h1>KEY CONTROL Judge</h1>
      <p className="lede">
        Per-pilot lap marshalling for RotorHazard. One judge, one pilot, two buttons.
      </p>

      <div className="card">
        <h2 style={{ fontSize: 'var(--text-lg)', margin: '0 0 var(--space-2)' }}>
          Judges
        </h2>
        <p className="muted" style={{ margin: 0 }}>
          Open the personal link the race director gave you. It already knows which pilot you
          are watching, so there is nothing to type and nothing to sign in to.
        </p>
      </div>

      <div className="card">
        <h2 style={{ fontSize: 'var(--text-lg)', margin: '0 0 var(--space-2)' }}>
          Race directors
        </h2>
        <p className="muted" style={{ marginTop: 0 }}>
          Turn on Cloud judging in the KEY CONTROL panel of your RotorHazard server. It opens a
          room here and prints one link per occupied seat. Watch the whole room at{' '}
          <span className="mono">/r/&lt;room code&gt;</span>.
        </p>
        <form action="/r" method="get" className="field">
          <input
            name="room"
            inputMode="text"
            autoComplete="off"
            maxLength={6}
            placeholder="ROOM"
            aria-label="Room code"
          />
          <button className="btn btn-primary" type="submit">
            Open
          </button>
        </form>
      </div>

      {!usingDatabase && (
        <p className="muted">
          Running without a database: rooms live in one server instance only. Set DATABASE_URL
          before a real event.
        </p>
      )}

      <p className="muted">
        <Link href="https://github.com/izicubed/RotorHazard-Key-Control">
          RotorHazard-Key-Control on GitHub
        </Link>
      </p>
    </main>
  );
}
