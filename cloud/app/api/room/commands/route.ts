import { NextRequest } from 'next/server';
import { authorize, badRequest, bearer, dequeue } from '@/lib/room';

export const runtime = 'nodejs';
export const dynamic = 'force-dynamic';

/** The plugin polls here for judge presses and clears the queue as it reads. */
export async function GET(req: NextRequest) {
  const secret = bearer(req);
  const room = (req.nextUrl.searchParams.get('room') ?? '').toUpperCase();
  if (!secret || !/^[0-9A-Z]{6}$/.test(room)) return badRequest('room and secret required', 401);
  if (!(await authorize(room, secret))) return badRequest('not your room', 403);

  const commands = await dequeue(room, 32);
  return Response.json({ ok: true, commands, serverTime: Date.now() });
}
