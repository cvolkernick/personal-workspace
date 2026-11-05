import Link from "next/link";
import { notFound } from "next/navigation";
import { Shell } from "@/components/shell";
import { LiveUnavailable } from "@/components/live-unavailable";
import { canSeeInvoice } from "@/lib/auth/access";
import { requireActor } from "@/lib/auth/session";
import { getDataSource } from "@/lib/data";
import { formatMoney } from "@/lib/format";

export const metadata = { title: "Review invoice" };

export default async function InvoiceReviewPage({
  params,
  searchParams,
}: {
  params: Promise<{ invoiceId: string }>;
  searchParams: Promise<{ notice?: string }>;
}) {
  const actor = await requireActor();
  if (actor === "live-unavailable") return <LiveUnavailable />;
  const { invoiceId } = await params;
  const notice = (await searchParams).notice;
  const source = getDataSource();
  const [invoice, cars, allCars] = await Promise.all([
    source.getInvoice(invoiceId),
    source.listCars(actor.role === "admin" ? undefined : actor.investorId ?? undefined),
    source.listCars(),
  ]);
  if (!invoice || !canSeeInvoice(actor, invoice, allCars)) notFound();
  const suggestionVisible = cars.some((car) => car.id === invoice.extracted.suggestedCarId);
  const defaultCar = suggestionVisible ? invoice.extracted.suggestedCarId ?? cars[0]?.id : cars[0]?.id;

  return (
    <Shell actor={actor} active="invoices">
      <main id="main">
        <p>
          <Link href="/invoices">All invoices</Link>
        </p>
        <h1>Review before posting</h1>
        <p className="lede">
          Suggested reading. Edit the car, category, and amount before anything posts. Approving adds the expense and
          recalculates ROI.
        </p>
        {notice ? <p className="notice">Check the car, category, amount, and date, then approve again.</p> : null}
        <div className="panel" style={{ marginBottom: 16 }}>
          {/* Authenticated sample upload, not a static asset. */}
          {/* eslint-disable-next-line @next/next/no-img-element */}
          <img src={`/api/invoices/${invoice.id}/image`} alt="Uploaded invoice" style={{ maxWidth: "100%", height: "auto" }} />
          <pre>{invoice.extracted.rawText}</pre>
          <p>
            Suggested amount {formatMoney(invoice.extracted.amount)} from {invoice.extracted.vendor}.
            {suggestionVisible ? "" : " The suggestion is outside this fleet, so pick one of your cars."}
          </p>
          <p>Status: {invoice.reviewState === "pending" ? "Waiting for review" : "Approved"}</p>
        </div>
        {invoice.reviewState === "pending" ? (
          <form className="panel form-grid" action="/invoices/approve" method="post">
            <input type="hidden" name="invoiceId" value={invoice.id} />
            <label>
              Car
              <select name="carId" defaultValue={defaultCar} required>
                {cars.map((car) => (
                  <option key={car.id} value={car.id}>
                    {car.name}
                  </option>
                ))}
              </select>
            </label>
            <label>
              Category
              <select name="category" defaultValue="maintenance">
                <option value="maintenance">Maintenance</option>
                <option value="other">Other</option>
              </select>
            </label>
            <label>
              Amount
              <input name="amount" type="number" min="0" step="1" defaultValue={invoice.extracted.amount} required />
            </label>
            <label>
              Date
              <input name="date" type="date" defaultValue={invoice.extracted.date} required />
            </label>
            <label>
              Vendor
              <input name="vendor" type="text" defaultValue={invoice.extracted.vendor} required />
            </label>
            <div>
              <button type="submit" className="primary">
                Approve and post expense
              </button>
            </div>
          </form>
        ) : (
          <p>
            Posted to the car. <a href={`/cars/${invoice.carId}`}>Open the car</a>
          </p>
        )}
      </main>
    </Shell>
  );
}
export const runtime = "edge";
export const dynamic = "force-dynamic";
