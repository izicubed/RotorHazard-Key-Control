import type { Metadata, Viewport } from 'next';
import { Inter } from 'next/font/google';
import './globals.css';

// Self-hosted by Next, so a judge on a weak signal never waits on a font CDN.
const inter = Inter({ subsets: ['latin'], display: 'swap', variable: '--font-inter' });

export const metadata: Metadata = {
  title: 'KEY CONTROL Judge',
  description: 'Per-pilot lap marshalling for RotorHazard, from any phone.',
  manifest: '/manifest.webmanifest',
  appleWebApp: { capable: true, title: 'Judge', statusBarStyle: 'black-translucent' },
};

export const viewport: Viewport = {
  width: 'device-width',
  initialScale: 1,
  // Pinch-zoom stays available on purpose: never trap a judge in a layout
  // their eyes cannot read.
  viewportFit: 'cover',
  themeColor: '#0b0f1a',
};

export default function RootLayout({ children }: { children: React.ReactNode }) {
  return (
    <html lang="en" className={inter.variable}>
      <body>
        <div className="glow" aria-hidden="true" />
        {children}
      </body>
    </html>
  );
}
