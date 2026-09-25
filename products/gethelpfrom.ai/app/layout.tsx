import type { Metadata } from "next"
import "./globals.css"

export const metadata: Metadata = {
  title: "GetHelpFrom.ai",
  description: "Tap what's true. We'll suggest what to build.",
  robots: { index: false, follow: false },
}

export default function RootLayout({ children }: { children: React.ReactNode }) {
  return (
    <html lang="en">
      <body>
        {children}
        <footer className="legal">
          <p>Answers are kept 90 days. Email us and we delete them within 7 days. We do not train foundation models on your answers.</p>
          <p>We will show you a working slice of your #1 pick.</p>
        </footer>
      </body>
    </html>
  )
}
