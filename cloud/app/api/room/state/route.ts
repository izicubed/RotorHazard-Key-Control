import { NextRequest } from 'next/server';
import { MAX_SEATS, RoomState, authorize, badRequest, bearer, putState } from '@/lib/room';

export const runtime = 'nodejs';
export const dynamic = 'force-dynamic';

/** The plugin pushes a full snapshot whenever anything a judge can see changes. */
export async function POST(req: NextRequest) {
  const secret = bearer(req);
  const body = await req.json().catch(() => null);
  const room = String(body?.room ?? '').toUpperCase();
  if (!secret || !/^[0-9A-Z]{6}$/.test(room)) return badRequest('room and secret required', 401);
  if (!(await authorize(room, secret))) return badRequest('not your room', 403);

  const seats = Array.isArray(body?.seats) ? body.seats.slice(0, MAX_SEATS) : [];
  const state: RoomState = {
    seq: Number(body?.seq ?? 0),
    updatedAt: Date.now(),
    event: String(body?.event ?? ''),
    mode: body?.mode === 'manual' ? 'manual' : 'semi',
    raceStatus: Number(body?.raceStatus ?? 0),
    raceElapsed: Math.max(0, Number(body?.raceElapsed ?? 0)),
    raceLimit: Math.max(0, Number(body?.raceLimit ?? 0)),
    seats: seats.map((s: Record<string, unknown>) => ({
      seat: Number(s.seat),
      token: String(s.token),
      label: String(s.label ?? ''),
      callsign: s.callsign == null ? null : String(s.callsign),
      laps: Number(s.laps ?? 0),
      lastLap: s.lastLap == null ? null : String(s.lastLap),
      bestLap: s.bestLap == null ? null : String(s.bestLap),
    })),
  };
  if (state.seats.some((s) => !Number.isInteger(s.seat) || s.token.length < 8)) {
    return badRequest('every seat needs an integer index and a judge token');
  }

  await putState(room, state);
  return Response.json({ ok: true, serverTime: state.updatedAt });
}
