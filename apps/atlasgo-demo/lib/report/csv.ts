import type { MonthlyReport } from "./build";

export function reportToCsv(report: MonthlyReport): string {
  const lines = [
    ["car", "income", "maintenance", "other", "payment", "payout", "net"].join(","),
    ...report.cars.map((row) =>
      [cell(row.carName), row.income, row.maintenance, row.other, row.monthlyPayment, row.payout, row.net].join(","),
    ),
    [
      "TOTAL",
      report.totals.income,
      report.totals.maintenance,
      report.totals.other,
      report.totals.monthlyPayment,
      report.totals.payout,
      report.totals.net,
    ].join(","),
    ["platform", "gross", "fees", "income"].join(","),
    ...report.platforms.map((row) => [cell(row.platform), row.gross, row.fees, row.income].join(",")),
  ];
  return `${lines.join("\n")}\n`;
}

function cell(value: string): string {
  if (/[",\n]/.test(value)) return `"${value.replaceAll('"', '""')}"`;
  return value;
}
