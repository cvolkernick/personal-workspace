"use client";

export default function ErrorPage({ reset }: { error: Error; reset: () => void }) {
  return (
    <main id="main" className="wrap login">
      <h1>This page did not load</h1>
      <p className="lede">Reload the sample fleet and try the page again.</p>
      <button type="button" className="primary" onClick={() => reset()}>
        Try again
      </button>
    </main>
  );
}
