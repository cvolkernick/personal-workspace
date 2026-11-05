import { notFound } from "next/navigation";
import { Shell } from "@/components/shell";
import { LiveUnavailable } from "@/components/live-unavailable";
import { requireActor } from "@/lib/auth/session";
import { demoToday } from "@/lib/clock";
import { getDataSource } from "@/lib/data";
import { CAR_STATUSES } from "@/lib/data/types";
import { formatDay } from "@/lib/format";
import { buildBoard } from "@/lib/status/board";

export const metadata = { title: "Status board" };

export default async function BoardPage({
  searchParams,
}: {
  searchParams: Promise<{ status?: string; investor?: string }>;
}) {
  const actor = await requireActor();
  if (actor === "live-unavailable") return <LiveUnavailable />;
  if (actor.role !== "admin") notFound();
  const params = await searchParams;
  const statusFilter = CAR_STATUSES.find((status) => status === params.status) ?? "all";
  const source = getDataSource();
  const [statuses, cars, investors] = await Promise.all([
    source.getStatusBoard(),
    source.listCars(),
    source.listInvestors(),
  ]);
  const investorFilter = investors.some((investor) => investor.id === params.investor) ? params.investor! : "all";
  const asOf = demoToday();
  const entries = buildBoard({ statuses, cars, investors, asOf }).filter((entry) => {
    if (statusFilter !== "all" && entry.status !== statusFilter) return false;
    if (investorFilter !== "all" && entry.investorId !== investorFilter) return false;
    return true;
  });

  return (
    <Shell actor={actor} active="board">
      <main id="main">
        <h1>Car status</h1>
        <p className="lede">
          Sample board for {formatDay(asOf)}. Overdue means the expected return is before today. Due back today matches
          that date.
        </p>
        <form className="filters" method="get">
          <label>
            Status
            <select name="status" defaultValue={statusFilter} aria-label="Filter by status">
              <option value="all">All statuses</option>
              {CAR_STATUSES.map((status) => (
                <option key={status} value={status}>
                  {status}
                </option>
              ))}
            </select>
          </label>
          <label>
            Investor
            <select name="investor" defaultValue={investorFilter} aria-label="Filter by investor">
              <option value="all">All investors</option>
              {investors.map((investor) => (
                <option key={investor.id} value={investor.id}>
                  {investor.name}
                </option>
              ))}
            </select>
          </label>
          <button type="submit">Apply filters</button>
        </form>
        <div className="panel scroll">
          <table>
            <caption>{entries.length} cars on this view. Idle days count from the status start through today.</caption>
            <thead>
              <tr>
                <th>Car</th>
                <th>Investor</th>
                <th>Status</th>
                <th>Renter</th>
                <th>Start</th>
                <th>Expected return</th>
                <th>Flag</th>
                <th className="num">Idle days</th>
              </tr>
            </thead>
            <tbody>
              {entries.map((entry) => (
                <tr key={entry.carId} data-testid="board-row" data-status={entry.status} data-car={entry.carId}>
                  <td>
                    {entry.carName}
                    <span className="sub">{entry.plate}</span>
                  </td>
                  <td>{entry.investorName}</td>
                  <td>{entry.status}</td>
                  <td>{entry.renter ?? "—"}</td>
                  <td>{formatDay(entry.start)}</td>
                  <td>{formatDay(entry.expectedReturn)}</td>
                  <td data-testid={`flag-${entry.carId}`}>
                    {entry.overdue ? <span className="badge overdue">Overdue</span> : null}
                    {entry.dueBackToday ? <span className="badge due">Due back today</span> : null}
                    {!entry.overdue && !entry.dueBackToday ? "—" : null}
                  </td>
                  <td className="num">{entry.idleDays === null ? "—" : entry.idleDays}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </main>
    </Shell>
  );
}
export const runtime = "edge";
export const dynamic = "force-dynamic";
