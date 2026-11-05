import { type NextRequest } from "next/server";
import { SESSION_COOKIE } from "@/lib/auth/actors";
import { redirectTo } from "@/lib/http/redirect";

export const runtime = "edge";
export const dynamic = "force-dynamic";

export async function GET(request: NextRequest) {
  const response = redirectTo(request, "/");
  response.cookies.set(SESSION_COOKIE, "", { httpOnly: true, sameSite: "lax", path: "/", maxAge: 0 });
  return response;
}
