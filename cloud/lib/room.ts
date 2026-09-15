/* Room model for the judge relay.
 *
 * One room = one RotorHazard server. Each occupied seat gets a judge token;
 * the judge who holds that token sees exactly one pilot and can add or remove
 * that pilot's laps. The plugin is the only writer of room state and the only
 * consumer of the command queue.
 */
import { createHash, randomBytes, timingSafeEqual } from 'node:crypto';
import * as store from './store';

export const ROOM_TTL_SEC = 12 * 60 * 60; // a race day, refreshed on every write
export const CMD_CAP = 200;               // queued presses kept per room
export const MAX_SEATS = 8;

/** Crockford base32 without I, L, O, U - unambiguous when read aloud or typed. */
const ALPHABET = '0123456789ABCDEFGHJKMNPQRSTVWXYZ';

export function randomId(len: number): string {
  const bytes = randomBytes(len);
  let out = '';
  for (let i = 0; i < len; i++) out += ALPHABET[bytes[i] % ALPHABET.length];
  return out;
}

export type Seat = {
  seat: number;          // 0-based RotorHazard seat index
  token: string;         // judge token for this seat
  label: string;         // channel label, e.g. "R1"
  callsign: string | null;
  laps: number;          // counted (non-deleted) laps
  lastLap: string | null; // formatted time of the last counted lap
  bestLap: string | null;
};

export type RoomState = {
  seq: number;
  updatedAt: number;     // server clock, ms
  event: string;         // heat/class name shown to the judge
  mode: 'manual' | 'semi';
  raceStatus: number;    // RotorHazard RaceStatus: 0 ready 1 racing 2 done 3 stopped
  seats: Seat[];
};

export type Command = {
  id: string;
  seat: number;
  button: 'add' | 'del';
  /** ms between the judge's tap and the moment the request left their phone. */
  ageAtSend: number;
  /** server clock when the relay accepted it */
  recvAt: number;
  judge: string;         // token prefix, for the plugin's log line
};

const keyMeta = (room: string) => `kc:room:${room}:meta`;
const keyState = (room: string) => `kc:room:${room}:state`;
const keyCmds = (room: string) => `kc:room:${room}:cmds`;
const keyToken = (token: string) => `kc:token:${token}`;

type Meta = { room: string; secretHash: string; createdAt: number; version: string };

function hash(secret: string): string {
  return createHash('sha256').update(secret).digest('hex');
}

function sameSecret(a: string, b: string): boolean {
  const x = Buffer.from(a);
  const y = Buffer.from(b);
  return x.length === y.length && timingSafeEqual(x, y);
}

/** Create the room, or re-open it if the caller proves it owns it. */
export async function claim(room: string, secret: string, version: string) {
  const existing = await store.get<Meta>(keyMeta(room));
  if (existing && !sameSecret(existing.secretHash, hash(secret))) {
    return { ok: false as const, reason: 'taken' as const };
  }
  const meta: Meta = existing ?? { room, secretHash: hash(secret), createdAt: Date.now(), version };
  meta.version = version;
  await store.set(keyMeta(room), meta, ROOM_TTL_SEC);
  return { ok: true as const, createdAt: meta.createdAt };
}

export async function authorize(room: string, secret: string): Promise<boolean> {
  const meta = await store.get<Meta>(keyMeta(room));
  return Boolean(meta && sameSecret(meta.secretHash, hash(secret)));
}

export async function putState(room: string, state: RoomState): Promise<void> {
  await store.set(keyState(room), state, ROOM_TTL_SEC);
  // Token -> seat lookups, refreshed with the state so a re-seated judge link
  // follows the pilot without the judge reloading anything.
  await Promise.all(
    state.seats.map((s) => store.set(keyToken(s.token), { room, seat: s.seat }, ROOM_TTL_SEC)),
  );
}

export async function getState(room: string): Promise<RoomState | null> {
  return store.get<RoomState>(keyState(room));
}

export async function resolveToken(token: string) {
  return store.get<{ room: string; seat: number }>(keyToken(token));
}

export async function enqueue(room: string, cmd: Command): Promise<void> {
  await store.push(keyCmds(room), cmd, ROOM_TTL_SEC, CMD_CAP);
}

export async function dequeue(room: string, max: number): Promise<Command[]> {
  return store.drain<Command>(keyCmds(room), max);
}

export async function closeRoom(room: string): Promise<void> {
  const state = await getState(room);
  if (state) await Promise.all(state.seats.map((s) => store.del(keyToken(s.token))));
  await Promise.all([store.del(keyMeta(room)), store.del(keyState(room)), store.del(keyCmds(room))]);
}

/** Bearer secret from an Authorization header. */
export function bearer(req: Request): string | null {
  const header = req.headers.get('authorization') ?? '';
  const match = /^Bearer\s+(\S+)$/i.exec(header);
  return match ? match[1] : null;
}

export function badRequest(message: string, status = 400) {
  return Response.json({ ok: false, error: message }, { status });
}

/** The plugin refreshes its snapshot every few seconds; older than this and we
 *  tell the judge the timer is unreachable rather than showing stale laps. */
export const STALE_MS = 25_000;

/** Everything one judge's phone shows, or null if the link is not live. */
export async function judgeView(token: string) {
  const link = await resolveToken(token);
  if (!link) return null;
  const state = await getState(link.room);
  if (!state) return null;
  const seat = state.seats.find((s) => s.token === token);
  if (!seat) return null;

  const age = Date.now() - state.updatedAt;
  return {
    ok: true as const,
    room: link.room,
    event: state.event,
    mode: state.mode,
    raceStatus: state.raceStatus,
    timerOnline: age < STALE_MS,
    timerAge: Math.round(age / 1000),
    seq: state.seq,
    seat: seat.seat,
    label: seat.label,
    callsign: seat.callsign,
    laps: seat.laps,
    lastLap: seat.lastLap,
    bestLap: seat.bestLap,
    serverTime: Date.now(),
  };
}
