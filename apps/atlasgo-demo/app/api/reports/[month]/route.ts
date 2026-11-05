import { readActor } from "@/lib/auth/session";
import { getDataSource } from "@/lib/data";
import { loadMonthlyReport } from "@/lib/fleet/load";
import { reportToCsv } from "@/lib/report/csv";
import { HISTORY_MONTHS } from "@/lib/roi/calc";

export async function GET(request: Request, context: { params: Promise<{ month: string }> }) {
  const actor = await readActor();
  if (actor === "live-unavailable") return new Response("Live data is not connected", { status: 501 });
  if (!actor) return new Response("Sign in required", { status: 401 });
  const { month } = await context.params;
  if (!(HISTORY_MONTHS as readonly string[]).includes(month)) return new Response("Unknown month", { status: 404 });

  const source = getDataSource();
  let investorId = actor.investorId;
  if (actor.role === "admin") {
    const investors = await source.listInvestors();
    const requested = new URL(request.url).searchParams.get("investor");
    investorId = requested && investors.some((item) => item.id === requested) ? requested : investors[0]?.id ?? null;
  }
  if (!investorId) return new Response("Unknown investor", { status: 404 });
  const report = await loadMonthlyReport(source, investorId, month);
  if (!report) return new Response("Unknown investor", { status: 404 });
  return new Response(reportToCsv(report), {
    headers: {
      "Content-Type": "text/csv; charset=utf-8",
      "Content-Disposition": `attachment; filename="atlasgo-${month}.csv"`,
      "Cache-Control": "no-store",
    },
  });
}
export const runtime = "edge";
