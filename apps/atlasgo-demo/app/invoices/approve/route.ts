import { type NextRequest } from "next/server";
import { approveInvoice } from "@/app/invoices/actions";
import { redirectTo } from "@/lib/http/redirect";

export const runtime = "edge";
export const dynamic = "force-dynamic";

export async function POST(request: NextRequest) {
  const target = await approveInvoice(await request.formData());
  return redirectTo(request, target);
}
