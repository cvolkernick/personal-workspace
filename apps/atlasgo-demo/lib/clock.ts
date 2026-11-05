export const DEMO_TODAY = "2026-11-05";

export function demoToday(): string {
  return process.env.ATLASGO_TODAY || DEMO_TODAY;
}
