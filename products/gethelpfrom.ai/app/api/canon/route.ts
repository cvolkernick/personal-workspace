import { NextResponse } from "next/server"
import { canonReport } from "../../../lib/canon.js"

export const runtime = "nodejs"

export async function GET() {
  return NextResponse.json(canonReport())
}
