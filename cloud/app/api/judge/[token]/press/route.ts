import { NextRequest } from 'next/server';
import { Command, badRequest, enqueue, judgeView, randomId, resolveToken } from '@/lib/room';

export const runtime = 'nodejs';
export const dynamic = 'force-dynamic';

/** A judge's tap. The tap's own age travels with it, so relay latency never
 *  moves the lap time: the plugin records the lap at `now - age`, not at the
 *  moment the message happens to arrive. */
export async function POST(req: NextRequest, ctx: { params: Promise<{ token: string }> }) {
  const { token } = await ctx.params;
  const link = await resolveToken(token);
  if (!link) return badRequest('this judge link is not active', 404);

  const body = await req.json().catch(() => null);
  const button = body?.button;
  if (button !== 'add' && button !== 'del') return badRequest('button must be add or del');

  const presses = Array.isArray(body?.presses) ? body.presses : [{ button, ageAtSend: body?.ageAtSend }];
  const recvAt = Date.now();
  const queued: string[] = [];

  for (const press of presses.slice(0, 20)) {
    const kind = press?.button === 'del' ? 'del' : 'add';
    const cmd: Command = {
      id: randomId(8),
      seat: link.seat,
      button: kind,
      ageAtSend: clampAge(press?.ageAtSend),
      recvAt,
      judge: token.slice(0, 4),
    };
    await enqueue(link.room, cmd);
    queued.push(cmd.id);
  }

  // Answer with the fresh view so the judge's counter settles without a
  // second round trip.
  const view = await judgeView(token);
  return Response.json({ ok: true, queued, ...(view ?? {}) });
}

/** A tap older than 30 s is a stale offline replay we cannot place on the race
 *  clock with any confidence; treat it as "just now" and let the marshal fix it. */
function clampAge(value: unknown): number {
  const n = Number(value);
  if (!Number.isFinite(n) || n < 0) return 0;
  return Math.min(30_000, Math.round(n));
}
