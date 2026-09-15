import { NextRequest } from 'next/server';
import { badRequest, judgeView } from '@/lib/room';

export const runtime = 'nodejs';
export const dynamic = 'force-dynamic';

export async function GET(_req: NextRequest, ctx: { params: Promise<{ token: string }> }) {
  const { token } = await ctx.params;
  const view = await judgeView(token);
  if (!view) return badRequest('this judge link is not active', 404);
  return Response.json(view);
}
