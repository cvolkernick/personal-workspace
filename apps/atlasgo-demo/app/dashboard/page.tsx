import { Shell } from "@/components/shell";
import { LiveUnavailable } from "@/components/live-unavailable";
import { requireActor } from "@/lib/auth/session";
import { getDataSource } from "@/lib/data";
import { loadDashboard } from "@/lib/fleet/load";
import { formatMoney, formatMonth, formatPercent } from "@/lib/format";
import { HISTORY_MONTHS, monthFigures, selectedMonth } from "@/lib/roi/calc";

export const metadata = { title: "Dashboard" };

export default async function DashboardPage({
  searchParams,
}: {
  searchParams: Promise<{ month?: string }>;
}) {
  const actor = await requireActor();
  if (actor === "live-unavailable") return <LiveUnavailable />;
  const month = selectedMonth((await searchParams).month);
  const source = getDataSource();
  const [rows, investors] = await Promise.all([
    loadDashboard(source, actor.role === "admin" ? undefined : actor.investorId ?? undefined),
    source.listInvestors(),
  ]);
  const investorNames = new Map(investors.map((investor) => [investor.id, investor.name]));
  const totals = rows.reduce(
    (sum, row) => {
      const figures = monthFigures(row.roi, month);
      return {
        purchase: sum.purchase + row.car.purchaseCost,
        capital: sum.capital + row.roi.capital,
        cumulative: sum.cumulative + row.roi.cumulativeNet,
        income: sum.income + (figures?.income ?? 0),
        maintenance: sum.maintenance + (figures?.maintenance ?? 0),
        other: sum.other + (figures?.other ?? 0),
        payment: sum.payment + (figures?.monthlyPayment ?? 0),
        net: sum.net + (figures?.net ?? 0),
        hits: sum.hits + (row.roi.roiHit ? 1 : 0),
        alerts: sum.alerts + (figures?.maintenanceAlert ? 1 : 0),
      };
    },
    {
      purchase: 0,
      capital: 0,
      cumulative: 0,
      income: 0,
      maintenance: 0,
      other: 0,
      payment: 0,
      net: 0,
      hits: 0,
      alerts: 0,
    },
  );
  const fleetRatio = totals.capital === 0 ? 0 : totals.cumulative / totals.capital;

  return (
    <Shell actor={actor} active="dashboard">
      <main id="main">
        <h1>{actor.role === "admin" ? "All sample cars" : `${actor.name}'s fleet`}</h1>
        <p className="lede">
          Monthly columns use {formatMonth(month)}. Cumulative net, ROI, and the ROI hit badge use May–October 2026.
          Capital is purchase cost plus down payment.
        </p>
        <nav className="months" aria-label="Month">
          {HISTORY_MONTHS.map((item) => (
            <a key={item} href={`/dashboard?month=${item}`} aria-current={item === month ? "page" : undefined}>
              {formatMonth(item)}
            </a>
          ))}
        </nav>
        <section className="stats" aria-label="Fleet summary">
          <article className="panel stat">
            <span>Cumulative net</span>
            <b data-testid="fleet-cumulative">{formatMoney(totals.cumulative)}</b>
          </article>
          <article className="panel stat">
            <span>Fleet ROI</span>
            <b>{formatPercent(fleetRatio)}</b>
          </article>
          <article className="panel stat">
            <span>ROI hit</span>
            <b>
              {totals.hits} of {rows.length}
            </b>
          </article>
          <article className="panel stat">
            <span>Maintenance alerts</span>
            <b>{totals.alerts}</b>
          </article>
        </section>
        <div className="panel scroll">
          <table>
            <caption>
              A maintenance alert means maintenance is more than 25% of that car&apos;s gross for the selected month.
              ROI hit means cumulative net has reached capital.
            </caption>
            <thead>
              <tr>
                <th>Car</th>
                {actor.role === "admin" ? <th>Investor</th> : null}
                <th className="num">Purchase</th>
                <th className="num">Payment</th>
                <th className="num">Income</th>
                <th className="num">Maintenance</th>
                <th className="num">Other</th>
                <th className="num">Net</th>
                <th className="num">Cumulative</th>
                <th className="num">ROI</th>
                <th>Breakeven</th>
                <th>Flags</th>
              </tr>
            </thead>
            <tbody>
              {rows.map((row) => {
                const figures = monthFigures(row.roi, month);
                return (
                  <tr key={row.car.id}>
                    <td>
                      <a href={`/cars/${row.car.id}?month=${month}`}>{row.car.name}</a>
                      <span className="sub">
                        {row.car.year} {row.car.make} {row.car.model}
                      </span>
                    </td>
                    {actor.role === "admin" ? <td>{investorNames.get(row.car.investorId)}</td> : null}
                    <td className="num">
                      {formatMoney(row.car.purchaseCost)}
                      <span className="sub">
                        Down {formatMoney(row.car.downPayment)} · Capital {formatMoney(row.roi.capital)}
                      </span>
                    </td>
                    <td className="num">{formatMoney(row.car.monthlyPayment)}</td>
                    <td className="num">{formatMoney(figures?.income ?? 0)}</td>
                    <td className="num" data-testid={`maint-${row.car.id}`}>
                      {formatMoney(figures?.maintenance ?? 0)}
                    </td>
                    <td className="num">{formatMoney(figures?.other ?? 0)}</td>
                    <td className="num">{formatMoney(figures?.net ?? 0)}</td>
                    <td className="num" data-testid={`cumulative-${row.car.id}`}>
                      {formatMoney(row.roi.cumulativeNet)}
                    </td>
                    <td className="num">{formatPercent(row.roi.roiRatio)}</td>
                    <td>
                      {row.roi.monthsToBreakeven === null || !row.roi.breakevenMonth
                        ? "Not in this window"
                        : `${row.roi.monthsToBreakeven} mo · ${formatMonth(row.roi.breakevenMonth)}`}
                    </td>
                    <td>
                      <div className="badges">
                        {row.roi.roiHit ? (
                          <span className="badge hit" data-testid={`roi-badge-${row.car.id}`}>
                            ROI hit
                          </span>
                        ) : null}
                        {figures?.maintenanceAlert ? (
                          <span className="badge alert" data-testid={`maint-alert-${row.car.id}`}>
                            Maintenance alert
                          </span>
                        ) : null}
                      </div>
                    </td>
                  </tr>
                );
              })}
            </tbody>
            <tfoot>
              <tr className="total" data-testid="fleet-total">
                <td>Fleet total</td>
                {actor.role === "admin" ? <td /> : null}
                <td className="num">
                  {formatMoney(totals.purchase)}
                  <span className="sub">Capital {formatMoney(totals.capital)}</span>
                </td>
                <td className="num">{formatMoney(totals.payment)}</td>
                <td className="num">{formatMoney(totals.income)}</td>
                <td className="num">{formatMoney(totals.maintenance)}</td>
                <td className="num">{formatMoney(totals.other)}</td>
                <td className="num">{formatMoney(totals.net)}</td>
                <td className="num">{formatMoney(totals.cumulative)}</td>
                <td className="num">{formatPercent(fleetRatio)}</td>
                <td>—</td>
                <td>{totals.cumulative >= totals.capital ? "ROI hit" : ""}</td>
              </tr>
            </tfoot>
          </table>
        </div>
      </main>
    </Shell>
  );
}
