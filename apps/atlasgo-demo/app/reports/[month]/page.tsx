import Link from "next/link";
import { notFound } from "next/navigation";
import { PrintButton } from "@/components/print-button";
import { Shell } from "@/components/shell";
import { LiveUnavailable } from "@/components/live-unavailable";
import { requireActor } from "@/lib/auth/session";
import { getDataSource } from "@/lib/data";
import { loadMonthlyReport } from "@/lib/fleet/load";
import { formatMoney, formatMonth } from "@/lib/format";
import { HISTORY_MONTHS } from "@/lib/roi/calc";

export const metadata = { title: "Monthly report" };

export default async function ReportMonthPage({
  params,
  searchParams,
}: {
  params: Promise<{ month: string }>;
  searchParams: Promise<{ investor?: string }>;
}) {
  const actor = await requireActor();
  if (actor === "live-unavailable") return <LiveUnavailable />;
  const { month } = await params;
  if (!(HISTORY_MONTHS as readonly string[]).includes(month)) notFound();
  const query = await searchParams;
  const source = getDataSource();
  let investorId = actor.investorId;
  if (actor.role === "admin") {
    const investors = await source.listInvestors();
    investorId = query.investor && investors.some((item) => item.id === query.investor) ? query.investor : investors[0]?.id ?? null;
  }
  if (!investorId) notFound();
  const report = await loadMonthlyReport(source, investorId, month);
  if (!report) notFound();
  const csvHref =
    actor.role === "admin"
      ? `/api/reports/${month}?investor=${investorId}`
      : `/api/reports/${month}`;

  return (
    <Shell actor={actor} active="reports">
      <main id="main">
        <p>
          <Link href="/reports">All months</Link>
        </p>
        <h1>
          {report.investorName} · {formatMonth(month)}
        </h1>
        <p className="lede">
          Payout is income minus maintenance and other expenses. Net is payout minus the monthly payment.
        </p>
        <div className="months no-print">
          <a className="button primary" href={csvHref} data-testid="csv-download">
            Download CSV
          </a>
          <PrintButton />
        </div>
        <section className="stats" aria-label="Report totals">
          <article className="panel stat">
            <span>Income</span>
            <b>{formatMoney(report.totals.income)}</b>
          </article>
          <article className="panel stat">
            <span>Payout</span>
            <b>{formatMoney(report.totals.payout)}</b>
          </article>
          <article className="panel stat">
            <span>Net</span>
            <b data-testid="report-net">{formatMoney(report.totals.net)}</b>
          </article>
          <article className="panel stat">
            <span>Monthly payments</span>
            <b>{formatMoney(report.totals.monthlyPayment)}</b>
          </article>
        </section>
        <h2>Cars</h2>
        <div className="panel scroll">
          <table>
            <caption>Totals equal the sum of the car rows.</caption>
            <thead>
              <tr>
                <th>Car</th>
                <th className="num">Income</th>
                <th className="num">Maintenance</th>
                <th className="num">Other</th>
                <th className="num">Payment</th>
                <th className="num">Payout</th>
                <th className="num">Net</th>
              </tr>
            </thead>
            <tbody>
              {report.cars.map((row) => (
                <tr key={row.carId}>
                  <td>{row.carName}</td>
                  <td className="num">{formatMoney(row.income)}</td>
                  <td className="num">{formatMoney(row.maintenance)}</td>
                  <td className="num">{formatMoney(row.other)}</td>
                  <td className="num">{formatMoney(row.monthlyPayment)}</td>
                  <td className="num">{formatMoney(row.payout)}</td>
                  <td className="num">{formatMoney(row.net)}</td>
                </tr>
              ))}
            </tbody>
            <tfoot>
              <tr className="total">
                <td>Total</td>
                <td className="num">{formatMoney(report.totals.income)}</td>
                <td className="num">{formatMoney(report.totals.maintenance)}</td>
                <td className="num">{formatMoney(report.totals.other)}</td>
                <td className="num">{formatMoney(report.totals.monthlyPayment)}</td>
                <td className="num">{formatMoney(report.totals.payout)}</td>
                <td className="num">{formatMoney(report.totals.net)}</td>
              </tr>
            </tfoot>
          </table>
        </div>
        <h2 style={{ marginTop: 24 }}>By platform label</h2>
        <div className="panel scroll">
          <table>
            <thead>
              <tr>
                <th>Platform</th>
                <th className="num">Gross</th>
                <th className="num">Fees</th>
                <th className="num">Income</th>
              </tr>
            </thead>
            <tbody>
              {report.platforms.map((row) => (
                <tr key={row.platform}>
                  <td>{row.platform}</td>
                  <td className="num">{formatMoney(row.gross)}</td>
                  <td className="num">{formatMoney(row.fees)}</td>
                  <td className="num">{formatMoney(row.income)}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
        <h2 style={{ marginTop: 24 }}>Expenses by category</h2>
        <ul className="expense-list panel">
          {report.expensesByCategory.map((row) => (
            <li key={row.category}>
              <span>{row.category === "maintenance" ? "Maintenance" : "Other"}</span>
              <span>{formatMoney(row.amount)}</span>
            </li>
          ))}
        </ul>
      </main>
    </Shell>
  );
}
