import { NextResponse, type NextRequest } from "next/server";

// Edge `request.url` is rewritten to localhost inside `next start`, so a redirect
// built from it leaves 127.0.0.1. Prefer the host the browser actually used.
export function redirectTo(request: NextRequest, pathname: string): NextResponse {
  const host = request.headers.get("x-forwarded-host") ?? request.headers.get("host");
  const proto = request.headers.get("x-forwarded-proto") ?? request.nextUrl.protocol.replace(/:$/, "");
  const base = host ? `${proto}://${host}` : request.url;
  const response = NextResponse.redirect(new URL(pathname, base), 303);
  response.headers.set("Cache-Control", "no-store");
  return response;
}
