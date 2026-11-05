const MONTHS = [
  "January",
  "February",
  "March",
  "April",
  "May",
  "June",
  "July",
  "August",
  "September",
  "October",
  "November",
  "December",
];

export function formatMoney(value: number): string {
  const whole = Number.isInteger(value);
  return new Intl.NumberFormat("en-US", {
    style: "currency",
    currency: "USD",
    minimumFractionDigits: whole ? 0 : 2,
    maximumFractionDigits: 2,
  }).format(value);
}

export function formatPercent(ratio: number): string {
  const pct = Math.round(ratio * 1000) / 10;
  return `${pct.toFixed(1)}%`;
}

export function formatMonth(month: string): string {
  const [year, mon] = month.split("-").map(Number);
  return `${MONTHS[mon - 1]} ${year}`;
}

export function formatDay(iso: string | null): string {
  if (!iso) return "—";
  const [year, month, day] = iso.split("-");
  return `${MONTHS[Number(month) - 1].slice(0, 3)} ${Number(day)}, ${year}`;
}
