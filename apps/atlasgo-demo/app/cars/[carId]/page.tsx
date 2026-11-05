import { notFound } from "next/navigation";
import { Shell } from "@/components/shell";
import { LiveUnavailable } from "@/components/live-unavailable";
import { canSeeCar } from "@/lib/auth/access";
import { requireActor } from "@/lib/auth/session";
import { getDataSource } from "@/lib/data";
import { formatDay, formatMoney, formatMonth, formatPercent } from "@/lib/format";
import { HISTORY_MONTHS, buildCarRoi, monthFigures, selectedMonth } from "@/lib/roi/calc";

export const metadata = { title: "Car" };

export default async function CarPage({
  params,
  searchParams,
}: {
  params: Promise<{ carId: string }>;
  searchParams: Promise<{ month?: string }>;
}) {
  const actor = await requireActor();
  if (actor === "live-unavailable") return <LiveUnavailable />;
  const { carId } = await params;
  const month = selectedMonth((await searchParams).month);
  const source = getDataSource();
  const car = await source.getCar(carId);
  if (!car || !canSeeCar(actor, car)) notFound();
  const [trips, expenses] = await Promise.all([source.listTrips(car.id), source.listExpenses(car.id)]);
  const roi = buildCarRoi(car, trips, expenses, HISTORY_MONTHS);
  const figures = monthFigures(roi, month);
  const monthExpenses = expenses.filter((expense) => expense.date.startsWith(month));

  return (
    <Shell actor={actor} active="dashboard">
      <main id="main">
        <p>
          <a href={`/dashboard?month=${month}`}>All cars</a>
        </p>
        <h1>{car.name}</h1>
        <p className="lede">
          {car.year} {car.make} {car.model}. Capital {formatMoney(roi.capital)} is purchase {formatMoney(car.purchaseCost)}{" "}
          plus down payment {formatMoney(car.downPayment)}.
        </p>
        <div className="badges" style={{ marginBottom: 16 }}>
          {roi.roiHit ? (
            <span className="badge hit" data-testid={`roi-badge-${car.id}`}>
              ROI hit
            </span>
          ) : (
            <span className="badge quiet">ROI not reached</span>
          )}
          {figures?.maintenanceAlert ? (
            <span className="badge alert" data-testid={`maint-alert-${car.id}`}>
              Maintenance alert
            </span>
          ) : null}
        </div>
        <section className="stats" aria-label="Car summary">
          <article className="panel stat">
            <span>Cumulative net</span>
            <b data-testid={`cumulative-${car.id}`}>{formatMoney(roi.cumulativeNet)}</b>
          </article>
          <article className="panel stat">
            <span>ROI</span>
            <b>{formatPercent(roi.roiRatio)}</b>
          </article>
          <article className="panel stat">
            <span>Breakeven</span>
            <b>
              {roi.monthsToBreakeven === null || !roi.breakevenMonth
                ? "Not yet"
                : `${roi.monthsToBreakeven} mo`}
            </b>
          </article>
          <article className="panel stat">
            <span>{formatMonth(month)} maintenance</span>
            <b data-testid="month-maintenance">{formatMoney(figures?.maintenance ?? 0)}</b>
          </article>
        </section>
        <nav className="months" aria-label="Month">
          {HISTORY_MONTHS.map((item) => (
            <a key={item} href={`/cars/${car.id}?month=${item}`} aria-current={item === month ? "page" : undefined}>
              {formatMonth(item)}
            </a>
          ))}
        </nav>
        <div className="panel scroll">
          <table>
            <caption>Net is income minus maintenance, other expenses, and the monthly payment.</caption>
            <thead>
              <tr>
                <th>Month</th>
                <th className="num">Income</th>
                <th className="num">Maintenance</th>
                <th className="num">Other</th>
                <th className="num">Payment</th>
                <th className="num">Net</th>
                <th>Alert</th>
              </tr>
            </thead>
            <tbody>
              {roi.months.map((item) => (
                <tr key={item.month}>
                  <td>{formatMonth(item.month)}</td>
                  <td className="num">{formatMoney(item.income)}</td>
                  <td className="num">{formatMoney(item.maintenance)}</td>
                  <td className="num">{formatMoney(item.other)}</td>
                  <td className="num">{formatMoney(item.monthlyPayment)}</td>
                  <td className="num">{formatMoney(item.net)}</td>
                  <td>{item.maintenanceAlert ? "Maintenance alert" : "—"}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
        <h2 style={{ marginTop: 24 }}>{formatMonth(month)} expenses</h2>
        <ul className="expense-list panel">
          {monthExpenses.map((expense) => (
            <li key={expense.id} data-source={expense.source}>
              <span>
                {expense.category === "maintenance" ? "Maintenance" : "Other"}
                <span className="sub">{expense.source === "invoice" ? "Posted from a reviewed invoice" : "Sample entry"} · {formatDay(expense.date)}</span>
              </span>
              <span className="num">{formatMoney(expense.amount)}</span>
            </li>
          ))}
          {monthExpenses.length === 0 ? <li>No expenses this month.</li> : null}
        </ul>
      </main>
    </Shell>
  );
}
export const runtime = "edge";
export const dynamic = "force-dynamic";
