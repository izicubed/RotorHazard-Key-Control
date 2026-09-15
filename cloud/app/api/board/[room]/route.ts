import { NextRequest } from 'next/server';
import { STALE_MS, badRequest, getState } from '@/lib/room';

export const runtime = 'nodejs';
export const dynamic = 'force-dynamic';

/** Read-only view of a whole room, for the race director's own screen.
 *  Judge tokens are never included here. */
export async function GET(_req: NextRequest, ctx: { params: Promise<{ room: string }> }) {
  const { room } = await ctx.params;
  const state = await getState(room.toUpperCase());
  if (!state) return badRequest('no such room', 404);
  const age = Date.now() - state.updatedAt;
  return Response.json({
    ok: true,
    room: room.toUpperCase(),
    event: state.event,
    mode: state.mode,
    raceStatus: state.raceStatus,
    timerOnline: age < STALE_MS,
    seats: state.seats.map(({ token, ...rest }) => ({ ...rest, judgeLinked: Boolean(token) })),
    serverTime: Date.now(),
  });
}
