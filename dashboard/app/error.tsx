'use client';

import { RotateCcw, TriangleAlert } from 'lucide-react';

export default function ErrorPage({ reset }: { error: Error & { digest?: string }; reset: () => void }) {
  return <section className="error-state"><span><TriangleAlert size={22}/></span><h2>Watcher could not load this view</h2><p>The data is safe. Retry the database query or inspect the application logs.</p><button className="primary-button" onClick={reset}><RotateCcw size={14}/>Try again</button></section>;
}
