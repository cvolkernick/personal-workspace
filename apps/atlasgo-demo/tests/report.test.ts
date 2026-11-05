import { expect, it } from "vitest";
import { MockDataSource } from "@/lib/data/mock-source";
import { buildMonthlyReport } from "@/lib/report/build";
import { reportToCsv } from "@/lib/report/csv";

async function reportFor(investorId: string, month: string) {
  const source = new MockDataSource();
  const investors = await source.listInvestors();
  const investor = investors.find((item) => item.id === investorId);
  if (!investor) throw new Error(investorId);
  const cars = await source.listCars(investorId);
  const bundles = await Promise.all(
    cars.map(async (car) => ({
      trips: await source.listTrips(car.id),
      expenses: await source.listExpenses(car.id),
    })),
  );
  return buildMonthlyReport({
    investor,
    month,
    cars,
    trips: bundles.flatMap((bundle) => bundle.trips),
    expenses: bundles.flatMap((bundle) => bundle.expenses),
  });
}

it("totals_reconcile", async () => {
  const months = ["2026-05", "2026-06", "2026-07", "2026-08", "2026-09", "2026-10"];
  for (const investorId of ["inv_avery", "inv_blake", "inv_casey"]) {
    for (const month of months) {
      const report = await reportFor(investorId, month);
      expect(report.totals.income).toBe(sum(report.cars.map((car) => car.income)));
      expect(report.totals.maintenance).toBe(sum(report.cars.map((car) => car.maintenance)));
      expect(report.totals.other).toBe(sum(report.cars.map((car) => car.other)));
      expect(report.totals.monthlyPayment).toBe(sum(report.cars.map((car) => car.monthlyPayment)));
      expect(report.totals.payout).toBe(sum(report.cars.map((car) => car.payout)));
      expect(report.totals.net).toBe(sum(report.cars.map((car) => car.net)));
      expect(report.totals.net).toBe(report.totals.payout - report.totals.monthlyPayment);
      const csv = reportToCsv(report);
      expect(csv).toContain(
        `TOTAL,${report.totals.income},${report.totals.maintenance},${report.totals.other},${report.totals.monthlyPayment},${report.totals.payout},${report.totals.net}`,
      );
    }
  }

  const october = await reportFor("inv_avery", "2026-10");
  expect(october.cars.map((car) => [car.carName, car.income, car.maintenance, car.other, car.monthlyPayment, car.payout, car.net])).toEqual([
    ["Harbor", 1260, 40, 20, 0, 1200, 1200],
    ["Lumen", 1000, 50, 30, 180, 920, 740],
    ["Sable", 540, 180, 30, 250, 330, 80],
  ]);
  expect(october.totals).toMatchObject({ income: 2800, maintenance: 270, other: 80, monthlyPayment: 430, payout: 2450, net: 2020 });
  expect(october.platforms.map((row) => [row.platform, row.gross, row.fees, row.income])).toEqual([
    ["Turo", 1400, 140, 1260],
    ["Upcar", 1100, 100, 1000],
    ["Direct", 600, 60, 540],
  ]);
  expect(october.expensesByCategory).toEqual([
    { category: "maintenance", amount: 270 },
    { category: "other", amount: 80 },
  ]);
  const csv = reportToCsv(october);
  expect(csv).toContain("Harbor,1260,40,20,0,1200,1200");
  expect(csv).toContain("TOTAL,2800,270,80,430,2450,2020");
  expect(csv).toContain("Turo,1400,140,1260");

  const may = await reportFor("inv_avery", "2026-05");
  expect(may.totals).toMatchObject({ income: 2890, maintenance: 130, other: 70, monthlyPayment: 430, payout: 2690, net: 2260 });
  const blake = await reportFor("inv_blake", "2026-10");
  expect(blake.totals).toMatchObject({ income: 2610, maintenance: 340, other: 85, monthlyPayment: 350, payout: 2185, net: 1835 });
  const casey = await reportFor("inv_casey", "2026-10");
  expect(casey.totals).toMatchObject({ income: 2120, maintenance: 100, other: 55, monthlyPayment: 220, payout: 1965, net: 1745 });
});

function sum(values: number[]): number {
  return values.reduce((total, value) => total + value, 0);
}
