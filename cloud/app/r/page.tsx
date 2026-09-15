import { redirect } from 'next/navigation';

export const dynamic = 'force-dynamic';

/** Landing-page room form lands here. */
export default async function RoomJump({
  searchParams,
}: {
  searchParams: Promise<{ room?: string }>;
}) {
  const { room } = await searchParams;
  const code = (room ?? '').trim().toUpperCase();
  redirect(/^[0-9A-Z]{6}$/.test(code) ? `/r/${code}` : '/');
}
