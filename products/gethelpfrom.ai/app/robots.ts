import type { MetadataRoute } from "next"

// Pre-launch: disallow all crawlers. Lift with the public gate (#646).
export default function robots(): MetadataRoute.Robots {
  return { rules: [{ userAgent: "*", disallow: "/" }] }
}
