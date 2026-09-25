import Funnel from "../components/Funnel"
import { canonReport, loadCanon } from "../lib/canon.js"

export const dynamic = "force-dynamic"

export default async function Page({
  searchParams,
}: {
  searchParams: Promise<{ ops?: string; s?: string }>
}) {
  const sp = await searchParams
  const canon = loadCanon()
  const report = canonReport()
  return (
    <>
      {sp.ops === "1" && report.drift ? (
        <p className="ops-banner" role="status">
          Canon files drifted from this build. Check /api/canon before trusting scores.
        </p>
      ) : null}
      <Funnel
        canon={{
          questions: canon.questions,
          scenarios: canon.scenarios,
          scoring: canon.scoring,
        }}
        resumeId={sp.s}
      />
    </>
  )
}
