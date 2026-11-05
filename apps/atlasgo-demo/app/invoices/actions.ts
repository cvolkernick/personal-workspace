"use server";

import { redirect } from "next/navigation";
import { canSeeCar, canSeeInvoice } from "@/lib/auth/access";
import { requireActor } from "@/lib/auth/session";
import { getDataSource } from "@/lib/data";
import type { ExpenseCategory } from "@/lib/data/types";

export async function uploadInvoice(formData: FormData) {
  const actor = await requireActor();
  if (actor === "live-unavailable") redirect("/");
  const file = formData.get("image");
  if (!(file instanceof File) || file.size === 0) redirect("/invoices?notice=missing");
  if (!file.type.startsWith("image/")) redirect("/invoices?notice=type");
  if (file.size > 1_500_000) redirect("/invoices?notice=size");
  const bytes = Buffer.from(await file.arrayBuffer());
  const invoice = await getDataSource().createInvoice({
    fileName: file.name || "upload.png",
    mediaType: file.type,
    bytesBase64: bytes.toString("base64"),
    ownerId: actor.id,
  });
  redirect(`/invoices/${invoice.id}`);
}

export async function approveInvoice(formData: FormData) {
  const actor = await requireActor();
  if (actor === "live-unavailable") redirect("/");
  const invoiceId = String(formData.get("invoiceId") ?? "");
  const carId = String(formData.get("carId") ?? "");
  const categoryRaw = String(formData.get("category") ?? "");
  const amount = Number(formData.get("amount"));
  const date = String(formData.get("date") ?? "");
  const vendor = String(formData.get("vendor") ?? "").trim() || "Unknown vendor";
  const category: ExpenseCategory | null =
    categoryRaw === "maintenance" || categoryRaw === "other" ? categoryRaw : null;
  if (!category || !Number.isFinite(amount) || amount < 0 || !/^\d{4}-\d{2}-\d{2}$/.test(date)) {
    redirect(`/invoices/${invoiceId}?notice=invalid`);
  }

  const source = getDataSource();
  const [invoice, cars] = await Promise.all([source.getInvoice(invoiceId), source.listCars()]);
  const car = cars.find((item) => item.id === carId);
  if (!invoice || !canSeeInvoice(actor, invoice, cars)) redirect("/invoices");
  if (!car || !canSeeCar(actor, car)) redirect(`/invoices/${invoiceId}?notice=car`);
  if (invoice.reviewState !== "pending") redirect(`/cars/${carId}?month=${date.slice(0, 7)}`);
  await source.approveInvoice(invoiceId, { carId, category, amount, date, vendor });
  redirect(`/cars/${carId}?month=${date.slice(0, 7)}`);
}
