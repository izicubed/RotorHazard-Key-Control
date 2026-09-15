import type { Metadata } from 'next';
import JudgeClient from './judge-client';

export const dynamic = 'force-dynamic';

export const metadata: Metadata = {
  title: 'Judge · KEY CONTROL',
  robots: { index: false, follow: false },
};

export default async function JudgePage({ params }: { params: Promise<{ token: string }> }) {
  const { token } = await params;
  return <JudgeClient token={token} />;
}
