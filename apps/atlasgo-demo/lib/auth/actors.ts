export const SESSION_COOKIE = "atlasgo_demo_session";

export type ActorRole = "investor" | "admin";

export interface Actor {
  id: string;
  role: ActorRole;
  investorId: string | null;
  name: string;
  email: string;
}

export const ADMIN_ACTOR: Actor = {
  id: "admin_morgan",
  role: "admin",
  investorId: null,
  name: "Morgan Ellis",
  email: "morgan.ellis@example.com",
};

export function actorFromInvestor(investor: { id: string; name: string; email: string }): Actor {
  return {
    id: investor.id,
    role: "investor",
    investorId: investor.id,
    name: investor.name,
    email: investor.email,
  };
}
