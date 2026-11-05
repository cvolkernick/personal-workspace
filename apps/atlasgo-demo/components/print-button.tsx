"use client";

export function PrintButton() {
  return (
    <button type="button" className="no-print" onClick={() => window.print()} data-testid="print-report">
      Print
    </button>
  );
}
