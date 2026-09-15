import { NextRequest } from 'next/server';
import QRCode from 'qrcode';

export const runtime = 'nodejs';

/** The judge link as a QR code, so a marshal points a phone at the race
 *  director's screen instead of retyping a token. Served outside /api so it
 *  can be cached: a token's code never changes. */
export async function GET(req: NextRequest, ctx: { params: Promise<{ token: string }> }) {
  const { token } = await ctx.params;
  if (!/^[0-9A-Za-z]{8,32}$/.test(token)) {
    return new Response('bad token', { status: 400 });
  }

  // Built here rather than taken from the caller, so this can only ever encode
  // one of our own judge links.
  const svg = await QRCode.toString(`${req.nextUrl.origin}/j/${token}`, {
    type: 'svg',
    errorCorrectionLevel: 'M',
    margin: 1,
    color: { dark: '#0b0f1a', light: '#ffffff' },
  });

  return new Response(svg, {
    headers: {
      'content-type': 'image/svg+xml; charset=utf-8',
      'cache-control': 'public, max-age=86400, immutable',
    },
  });
}
