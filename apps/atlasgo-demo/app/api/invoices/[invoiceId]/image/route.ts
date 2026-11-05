import { canSeeInvoice } from "@/lib/auth/access";
import { readActor } from "@/lib/auth/session";
import { getDataSource } from "@/lib/data";

export async function GET(_request: Request, context: { params: Promise<{ invoiceId: string }> }) {
  const actor = await readActor();
  if (actor === "live-unavailable") return new Response("Live data is not connected", { status: 501 });
  if (!actor) return new Response("Sign in required", { status: 401 });
  const { invoiceId } = await context.params;
  const source = getDataSource();
  const [invoice, cars, image] = await Promise.all([
    source.getInvoice(invoiceId),
    source.listCars(),
    source.getInvoiceImage(invoiceId),
  ]);
  if (!invoice || !image || !canSeeInvoice(actor, invoice, cars)) return new Response("Not found", { status: 404 });
  return new Response(Buffer.from(image.bytesBase64, "base64"), {
    headers: {
      "Content-Type": image.mediaType || "application/octet-stream",
      "Cache-Control": "no-store",
    },
  });
}
export const runtime = "edge";
