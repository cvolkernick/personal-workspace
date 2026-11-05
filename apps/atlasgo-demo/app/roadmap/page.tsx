import { Shell } from "@/components/shell";
import { LiveUnavailable } from "@/components/live-unavailable";
import { requireActor } from "@/lib/auth/session";
import { ROADMAP_ITEMS } from "@/lib/roadmap";

export const metadata = { title: "Coming next" };

export default async function RoadmapPage() {
  const actor = await requireActor();
  if (actor === "live-unavailable") return <LiveUnavailable />;

  return (
    <Shell actor={actor} active="roadmap">
      <main id="main">
        <h1>Coming next</h1>
        <p className="lede">These items are out of scope for this demo. The sample fleet stops at the screens you can open now.</p>
        <ol className="panel stack" data-testid="roadmap-list">
          {ROADMAP_ITEMS.map((item) => (
            <li key={item}>{item}</li>
          ))}
        </ol>
      </main>
    </Shell>
  );
}
export const runtime = "edge";
export const dynamic = "force-dynamic";
