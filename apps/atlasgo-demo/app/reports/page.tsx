import { Shell } from "@/components/shell";
import { LiveUnavailable } from "@/components/live-unavailable";
import { requireActor } from "@/lib/auth/session";
import { getDataSource } from "@/lib/data";
import { formatMonth } from "@/lib/format";
import { HISTORY_MONTHS } from "@/lib/roi/calc";

export const metadata = { title: "Reports" };

export default async function ReportsPage() {
  const actor = await requireActor();
  if (actor === "live-unavailable") return <LiveUnavailable />;
  const investors = actor.role === "admin" ? await getDataSource().listInvestors() : [];

  return (
    <Shell actor={actor} active="reports">
      <main id="main">
        <h1>Monthly reports</h1>
        <p className="lede">
          Each month lists income by platform label, expenses by category, payout, and net. Download the same numbers
          as CSV or print the page.
        </p>
        {actor.role === "investor" ? (
          <ul className="actor-list">
            {HISTORY_MONTHS.map((month) => (
              <li key={month}>
                <a className="button" href={`/reports/${month}`}>
                  {formatMonth(month)}
                </a>
              </li>
            ))}
          </ul>
        ) : (
          investors.map((investor) => (
            <section key={investor.id} style={{ marginBottom: 20 }}>
              <h2>{investor.name}</h2>
              <div className="months">
                {HISTORY_MONTHS.map((month) => (
                  <a key={month} href={`/reports/${month}?investor=${investor.id}`}>
                    {formatMonth(month)}
                  </a>
                ))}
              </div>
            </section>
          ))
        )}
      </main>
    </Shell>
  );
}
export const runtime = "edge";
export const dynamic = "force-dynamic";
