import type { Metadata } from "next";
import "./globals.css";

export const dynamic = "force-dynamic";

export const metadata: Metadata = {
  title: { default: "AtlasGO", template: "%s · AtlasGO" },
  description: "Sample fleet return demo.",
  robots: { index: false, follow: false },
};

export default function RootLayout({ children }: { children: React.ReactNode }) {
  return (
    <html lang="en">
      <body>
        <a className="skip" href="#main">
          Skip to content
        </a>
        {children}
        <footer className="demo wrap">Demo — sample data</footer>
      </body>
    </html>
  );
}
