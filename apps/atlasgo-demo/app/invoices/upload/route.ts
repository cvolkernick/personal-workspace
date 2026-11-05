import { type NextRequest } from "next/server";
import { uploadInvoice } from "@/app/invoices/actions";
import { redirectTo } from "@/lib/http/redirect";

export const runtime = "edge";
export const dynamic = "force-dynamic";

export async function POST(request: NextRequest) {
  const target = await uploadInvoice(await request.formData());
  return redirectTo(request, target);
}
