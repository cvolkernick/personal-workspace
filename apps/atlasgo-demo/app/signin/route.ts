import { type NextRequest } from "next/server";
import { getDataSource } from "@/lib/data";
import { ADMIN_ACTOR, SESSION_COOKIE } from "@/lib/auth/actors";
import { redirectTo } from "@/lib/http/redirect";

export const runtime = "edge";
export const dynamic = "force-dynamic";

// Plain GET sign-in for the sample picker: sets the session cookie and redirects.
// Replaces the server action, which returned 500 on Cloudflare Pages.
export async function GET(request: NextRequest) {
  const id = request.nextUrl.searchParams.get("actor") ?? "";
  const investors = await getDataSource().listInvestors();
  const allowed = id === ADMIN_ACTOR.id || investors.some((investor) => investor.id === id);
  const target = allowed ? (id === ADMIN_ACTOR.id ? "/board" : "/dashboard") : "/";
  const response = redirectTo(request, target);
  if (allowed) {
    response.cookies.set(SESSION_COOKIE, id, {
      httpOnly: true,
      sameSite: "lax",
      path: "/",
      secure: request.nextUrl.protocol === "https:",
      maxAge: 60 * 60 * 12,
    });
  }
  return response;
}
