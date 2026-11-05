import { redirect } from "next/navigation";
import { signIn } from "@/app/auth-actions";
import { LiveUnavailable } from "@/components/live-unavailable";
import { ADMIN_ACTOR } from "@/lib/auth/actors";
import { readActor } from "@/lib/auth/session";
import { getDataSource } from "@/lib/data";
import { NotImplementedError } from "@/lib/data/errors";

export default async function LoginPage() {
  const actor = await readActor();
  if (actor === "live-unavailable") return <LiveUnavailable />;
  if (actor) redirect(actor.role === "admin" ? "/board" : "/dashboard");

  let investors;
  try {
    investors = await getDataSource().listInvestors();
  } catch (error) {
    if (error instanceof NotImplementedError) return <LiveUnavailable />;
    throw error;
  }

  const choices = [
    ...investors.map((investor) => ({
      id: investor.id,
      name: investor.name,
      email: investor.email,
      detail: "Sample investor",
    })),
    {
      id: ADMIN_ACTOR.id,
      name: ADMIN_ACTOR.name,
      email: ADMIN_ACTOR.email,
      detail: "Sample admin",
    },
  ];

  return (
    <main id="main" className="wrap login">
      <p className="brand">AtlasGO</p>
      <h1>Fleet return, on one page</h1>
      <p className="lede">
        See each car after maintenance and the monthly payment, and whether it has earned back its capital.
        This sign-in is a sample picker. There is no password.
      </p>
      <ul className="actor-list">
        {choices.map((choice) => (
          <li key={choice.id}>
            <form action={signIn}>
              <input type="hidden" name="actorId" value={choice.id} />
              <button type="submit" data-actor={choice.id}>
                <strong>Continue as {choice.name}</strong>
                <span>
                  {choice.detail} · {choice.email}
                </span>
              </button>
            </form>
          </li>
        ))}
      </ul>
    </main>
  );
}
