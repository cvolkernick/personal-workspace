import type { Actor } from "@/lib/auth/actors";

const LINKS = [
  { href: "/dashboard", id: "dashboard", label: "Dashboard" },
  { href: "/reports", id: "reports", label: "Reports" },
  { href: "/invoices", id: "invoices", label: "Invoices" },
  { href: "/roadmap", id: "roadmap", label: "Coming next" },
] as const;

export function Shell({
  actor,
  active,
  children,
}: {
  actor: Actor;
  active: string;
  children: React.ReactNode;
}) {
  return (
    <>
      <header className="top wrap">
        <p className="brand">
          <a href="/dashboard">AtlasGO</a>
        </p>
        <nav className="nav" aria-label="Primary">
          {LINKS.map((link) => (
            <a key={link.id} href={link.href} aria-current={active === link.id ? "page" : undefined}>
              {link.label}
            </a>
          ))}
          {actor.role === "admin" ? (
            <a href="/board" aria-current={active === "board" ? "page" : undefined}>
              Status board
            </a>
          ) : null}
          <span className="who">{actor.name}</span>
          <a href="/signout">Sign out</a>
        </nav>
      </header>
      <div className="wrap">{children}</div>
    </>
  );
}
