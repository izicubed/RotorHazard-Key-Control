# KEY CONTROL judge relay

The phone half of [KEY CONTROL](../README.md): a small Next.js app that lets a
marshal add and remove laps for one pilot from anywhere with a signal, while
the RotorHazard timer stays behind whatever network it happens to be on.

Deployed at **https://judge.airmode.app**.

## How a press travels

```
judge's phone                 relay (Vercel)                 RotorHazard + plugin
  tap ADD LAP  ──POST──►  queue the press with its age  ◄──GET── poll (0.7 s racing, 3 s idle)
                          room state (pilots, laps)     ◄──POST── snapshot on every change
  poll view    ──GET──►   the seat behind this token
```

The timer only ever makes **outbound** requests, so no port forwarding, public
IP or inbound firewall rule is involved.

**The tap carries its own age.** The phone measures how long ago its judge
pressed, using a monotonic clock; the relay adds how long the command waited in
the queue; the plugin records the lap that far back on the race clock. A slow
network or a wrong phone clock therefore delays when the lap *appears*, and
never changes *when it happened*.

## Rooms, seats and tokens

* One **room** per RotorHazard server: a six-character code from an alphabet
  with no I, L, O or U, so it survives being read out over a radio.
* The plugin holds a **room secret** and is the only writer of room state and
  the only consumer of the press queue.
* Each occupied seat gets a **judge token**. Possession of the link is the
  judge's entire credential, and it reaches exactly one seat. Tokens are per
  channel, so a link keeps working as heats change.
* Everything expires 12 hours after the last write, refreshed on every push.
* `/r/<room>` is read-only and never carries tokens.

## Pages

| Path | Who | What |
|------|-----|------|
| `/j/<token>` | one judge | callsign, channel, pass count, last/best lap, ADD LAP and REMOVE LAP |
| `/r/<room>` | race director | every seat in the room, read only |
| `/` | anyone | what this is, plus a room-code box |

The judge page keeps the screen awake, vibrates on a press, follows the
viewer's light or dark theme, and holds presses locally while the phone has no
signal, sending them when it returns.

## API

Plugin routes take `Authorization: Bearer <room secret>`.

| Route | Method | Purpose |
|-------|--------|---------|
| `/api/room/claim` | POST | open or re-open a room (`409` if the code belongs to someone else) |
| `/api/room/state` | POST | full snapshot: pilots, channels, lap counts, race status |
| `/api/room/commands` | GET | take and clear the queued presses |
| `/api/room/close` | POST | revoke every judge link of the room |
| `/api/judge/<token>` | GET | one judge's view |
| `/api/judge/<token>/press` | POST | queue one tap, or a batch held while offline |
| `/api/board/<room>` | GET | the read-only board |

## Running it yourself

Any Postgres works; the deployed copy uses the Neon integration on Vercel.

```bash
npm install
export DATABASE_URL="postgres://..."   # or POSTGRES_URL
npm run dev
```

The two tables (`kc_kv`, `kc_cmd`) are created on first use. With no
`DATABASE_URL` the app falls back to an in-process Map so `next dev` runs with
nothing provisioned; that fallback is single-instance and is not a production
backend, and the landing page says so.

To deploy your own copy:

```bash
vercel link
vercel deploy --prod
```

Then point the timer at it: Settings → KEY CONTROL → **Judge relay address**.
