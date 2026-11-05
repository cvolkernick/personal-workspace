// Plain helpers called from POST route handlers (server actions fail on next-on-pages:
// "Invariant: Missing initURL"). Each returns the path to redirect to.
import { canSeeCar, canSeeInvoice } from "@/lib/auth/access";
import { readActor } from "@/lib/auth/session";
import { getDataSource } from "@/lib/data";
import type { ExpenseCategory } from "@/lib/data/types";

export async function uploadInvoice(formData: FormData): Promise<string> {
  const actor = await readActor();
  if (!actor || actor === "live-unavailable") return "/";
  const file = formData.get("image");
  if (!(file instanceof File) || file.size === 0) return "/invoices?notice=missing";
  if (!file.type.startsWith("image/")) return "/invoices?notice=type";
  if (file.size > 1_500_000) return "/invoices?notice=size";
  const bytes = Buffer.from(await file.arrayBuffer());
  const invoice = await getDataSource().createInvoice({
    fileName: file.name || "upload.png",
    mediaType: file.type,
    bytesBase64: bytes.toString("base64"),
    ownerId: actor.id,
  });
  return `/invoices/${invoice.id}`;
}

export async function approveInvoice(formData: FormData): Promise<string> {
  const actor = await readActor();
  if (!actor || actor === "live-unavailable") return "/";
  const invoiceId = String(formData.get("invoiceId") ?? "");
  const carId = String(formData.get("carId") ?? "");
  const categoryRaw = String(formData.get("category") ?? "");
  const amount = Number(formData.get("amount"));
  const date = String(formData.get("date") ?? "");
  const vendor = String(formData.get("vendor") ?? "").trim() || "Unknown vendor";
  const category: ExpenseCategory | null =
    categoryRaw === "maintenance" || categoryRaw === "other" ? categoryRaw : null;
  if (!category || !Number.isFinite(amount) || amount < 0 || !/^\d{4}-\d{2}-\d{2}$/.test(date)) {
    return `/invoices/${invoiceId}?notice=invalid`;
  }

  const source = getDataSource();
  const [invoice, cars] = await Promise.all([source.getInvoice(invoiceId), source.listCars()]);
  const car = cars.find((item) => item.id === carId);
  if (!invoice || !canSeeInvoice(actor, invoice, cars)) return "/invoices";
  if (!car || !canSeeCar(actor, car)) return `/invoices/${invoiceId}?notice=car`;
  if (invoice.reviewState !== "pending") return `/cars/${carId}?month=${date.slice(0, 7)}`;
  await source.approveInvoice(invoiceId, { carId, category, amount, date, vendor });
  return `/cars/${carId}?month=${date.slice(0, 7)}`;
}
