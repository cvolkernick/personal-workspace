import "server-only";
import { cookies } from "next/headers";
import { redirect } from "next/navigation";
import { getDataSource } from "@/lib/data";
import { NotImplementedError } from "@/lib/data/errors";
import { ADMIN_ACTOR, SESSION_COOKIE, actorFromInvestor, type Actor } from "./actors";

export async function sessionId(): Promise<string | null> {
  const jar = await cookies();
  return jar.get(SESSION_COOKIE)?.value || null;
}

export async function readActor(): Promise<Actor | null | "live-unavailable"> {
  const id = await sessionId();
  if (!id) return null;
  try {
    const investors = await getDataSource().listInvestors();
    if (id === ADMIN_ACTOR.id) return ADMIN_ACTOR;
    const investor = investors.find((item) => item.id === id);
    return investor ? actorFromInvestor(investor) : null;
  } catch (error) {
    if (error instanceof NotImplementedError) return "live-unavailable";
    throw error;
  }
}

export async function requireActor(): Promise<Actor | "live-unavailable"> {
  const actor = await readActor();
  if (actor === null) redirect("/");
  return actor;
}
