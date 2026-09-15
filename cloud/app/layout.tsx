import type { Metadata, Viewport } from 'next';
import './globals.css';

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
  themeColor: [
    { media: '(prefers-color-scheme: dark)', color: '#080b12' },
    { media: '(prefers-color-scheme: light)', color: '#f5f7fb' },
  ],
};

export default function RootLayout({ children }: { children: React.ReactNode }) {
  return (
    <html lang="en">
      <body>{children}</body>
    </html>
  );
}
