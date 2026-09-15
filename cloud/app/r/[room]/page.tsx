import type { Metadata } from 'next';
import BoardClient from './board-client';

export const dynamic = 'force-dynamic';

export const metadata: Metadata = {
  title: 'Room board - KEY CONTROL',
  robots: { index: false, follow: false },
};

export default async function BoardPage({ params }: { params: Promise<{ room: string }> }) {
  const { room } = await params;
  return <BoardClient room={room.toUpperCase()} />;
}
