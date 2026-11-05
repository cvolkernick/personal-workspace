"use server";

import { cookies } from "next/headers";
import { redirect } from "next/navigation";
import { getDataSource } from "@/lib/data";
import { ADMIN_ACTOR, SESSION_COOKIE } from "@/lib/auth/actors";

export async function signIn(formData: FormData) {
  const id = String(formData.get("actorId") ?? "");
  const investors = await getDataSource().listInvestors();
  const allowed = id === ADMIN_ACTOR.id || investors.some((investor) => investor.id === id);
  if (!allowed) redirect("/");
  const jar = await cookies();
  jar.set(SESSION_COOKIE, id, {
    httpOnly: true,
    sameSite: "lax",
    path: "/",
    secure: process.env.NODE_ENV === "production",
    maxAge: 60 * 60 * 12,
  });
  redirect(id === ADMIN_ACTOR.id ? "/board" : "/dashboard");
}

export async function signOut() {
  const jar = await cookies();
  jar.set(SESSION_COOKIE, "", {
    httpOnly: true,
    sameSite: "lax",
    path: "/",
    secure: process.env.NODE_ENV === "production",
    maxAge: 0,
  });
  redirect("/");
}
