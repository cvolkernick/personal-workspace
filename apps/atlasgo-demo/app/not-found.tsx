export default function NotFound() {
  return (
    <main id="main" className="wrap login">
      <h1>Not in this fleet</h1>
      <p className="lede">That page is not available for the signed-in sample account.</p>
      <p>
        <a className="button" href="/dashboard">
          Back to dashboard
        </a>
      </p>
    </main>
  );
}
