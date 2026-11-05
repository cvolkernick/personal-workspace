import { Shell } from "@/components/shell";
import { LiveUnavailable } from "@/components/live-unavailable";
import { canSeeInvoice } from "@/lib/auth/access";
import { requireActor } from "@/lib/auth/session";
import { getDataSource } from "@/lib/data";
import { formatMoney } from "@/lib/format";

export const metadata = { title: "Invoices" };

const NOTICES: Record<string, string> = {
  missing: "Choose an image before uploading.",
  type: "Upload an image file.",
  size: "That image is over 1.5 MB.",
};

export default async function InvoicesPage({
  searchParams,
}: {
  searchParams: Promise<{ notice?: string }>;
}) {
  const actor = await requireActor();
  if (actor === "live-unavailable") return <LiveUnavailable />;
  const notice = NOTICES[(await searchParams).notice ?? ""] ?? null;
  const source = getDataSource();
  const [invoices, cars] = await Promise.all([source.listInvoices(), source.listCars()]);
  const visible = invoices.filter((invoice) => canSeeInvoice(actor, invoice, cars));

  return (
    <Shell actor={actor} active="invoices">
      <main id="main">
        <h1>Invoice review</h1>
        <p className="lede">
          An upload is only a suggestion until you approve it. Nothing posts to a car before that review.
        </p>
        {notice ? <p className="notice">{notice}</p> : null}
        <form className="panel stack" action="/invoices/upload" method="post" encType="multipart/form-data">
          <label>
            Invoice image
            <input type="file" name="image" accept="image/*" required />
          </label>
          <button type="submit" className="primary">
            Upload for review
          </button>
        </form>
        <h2 style={{ marginTop: 24 }}>Uploads</h2>
        {visible.length === 0 ? <p>No invoices yet.</p> : null}
        <ul className="actor-list">
          {visible.map((invoice) => (
            <li key={invoice.id}>
              <a className="button" href={`/invoices/${invoice.id}`}>
                <strong>{invoice.extracted.vendor}</strong>
                <span>
                  {invoice.reviewState === "pending" ? "Waiting for review" : "Approved"} ·{" "}
                  {formatMoney(invoice.amount ?? invoice.extracted.amount)}
                </span>
              </a>
            </li>
          ))}
        </ul>
      </main>
    </Shell>
  );
}
export const runtime = "edge";
export const dynamic = "force-dynamic";
