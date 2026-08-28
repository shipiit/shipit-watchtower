import type { Metadata } from 'next';
import { Geist, Geist_Mono } from 'next/font/google';
import Script from 'next/script';
import { AppShell } from '@/components/app-shell';
import './globals.css';

const geistSans = Geist({
  variable: '--font-geist-sans',
  subsets: ['latin'],
});

const geistMono = Geist_Mono({
  variable: '--font-geist-mono',
  subsets: ['latin'],
});

export const metadata: Metadata = {
  title: 'Shipit Watcher — AI Observability & Governance',
  description: 'Trace, evaluate, govern, and optimize every AI system from one vendor-neutral control plane.',
};

const themeScript = `(function(){try{var t=localStorage.getItem('watcher-theme');if(t&&t!=='system'){document.documentElement.dataset.theme=t;document.documentElement.style.colorScheme=t}}catch(e){}})()`;

export default function RootLayout({
  children,
}: Readonly<{
  children: React.ReactNode;
}>) {
  return (
    <html lang="en" suppressHydrationWarning>
      <body
        className={`${geistSans.variable} ${geistMono.variable} antialiased`}
      >
        <Script id="watcher-theme" strategy="beforeInteractive">{themeScript}</Script>
        <AppShell>{children}</AppShell>
      </body>
    </html>
  );
}
