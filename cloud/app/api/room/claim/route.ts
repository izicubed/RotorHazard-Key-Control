import { NextRequest } from 'next/server';
import { badRequest, bearer, claim } from '@/lib/room';
import { usingDatabase } from "@/lib/store";

export const runtime = 'nodejs';
export const dynamic = 'force-dynamic';

/** The plugin opens (or re-opens) its room here on every startup. */
export async function POST(req: NextRequest) {
  const secret = bearer(req);
  if (!secret || secret.length < 16) return badRequest('missing bearer secret', 401);

  const body = await req.json().catch(() => null);
  const room = String(body?.room ?? '').toUpperCase();
  if (!/^[0-9A-Z]{6}$/.test(room)) return badRequest('room must be 6 characters');

  const result = await claim(room, secret, String(body?.version ?? '?'));
  if (!result.ok) return badRequest('that room code belongs to another server', 409);

  return Response.json({
    ok: true,
    room,
    createdAt: result.createdAt,
    serverTime: Date.now(),
    durable: usingDatabase,
  });
}
