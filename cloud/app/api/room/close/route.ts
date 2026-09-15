import { NextRequest } from 'next/server';
import { authorize, badRequest, bearer, closeRoom } from '@/lib/room';

export const runtime = 'nodejs';
export const dynamic = 'force-dynamic';

/** Revokes every judge link of a room. */
export async function POST(req: NextRequest) {
  const secret = bearer(req);
  const body = await req.json().catch(() => null);
  const room = String(body?.room ?? '').toUpperCase();
  if (!secret || !/^[0-9A-Z]{6}$/.test(room)) return badRequest('room and secret required', 401);
  if (!(await authorize(room, secret))) return badRequest('not your room', 403);
  await closeRoom(room);
  return Response.json({ ok: true });
}
