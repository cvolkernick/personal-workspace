import { beforeAll, describe, expect, it } from "vitest";
import type { Car, Expense, Trip } from "@/lib/data/types";
import { MockDataSource } from "@/lib/data/mock-source";
import {
  HISTORY_MONTHS,
  buildCarRoi,
  capitalInvested,
  isMaintenanceAlert,
  monthFigures,
  type CarRoi,
} from "@/lib/roi/calc";
import { FLEET_CAPITAL, FLEET_CUMULATIVE_NET, OCTOBER_NET_TOTAL, ROI_FIXTURE } from "./roi-fixture";

async function load() {
  const source = new MockDataSource();
  const cars = await source.listCars();
  const bundles = await Promise.all(
    cars.map(async (car) => {
      const [trips, expenses] = await Promise.all([source.listTrips(car.id), source.listExpenses(car.id)]);
      return { car, trips, expenses, roi: buildCarRoi(car, trips, expenses, HISTORY_MONTHS) };
    }),
  );
  return bundles;
}

let rows: { car: Car; trips: Trip[]; expenses: Expense[]; roi: CarRoi }[] = [];

beforeAll(async () => {
  rows = await load();
});

describe("roi", () => {
  it("matches_fixture", () => {
    expect(rows).toHaveLength(ROI_FIXTURE.length);
    for (const expected of ROI_FIXTURE) {
      const row = rows.find((item) => item.car.id === expected.carId);
      expect(row, expected.carId).toBeTruthy();
      const roi = row!.roi;
      expect(capitalInvested(row!.car)).toBe(expected.capital);
      expect(roi.capital).toBe(expected.capital);
      expect(roi.cumulativeNet).toBe(expected.cumulativeNet);
      expect(roi.roiRatio).toBeCloseTo(expected.cumulativeNet / expected.capital, 10);
      expect(roi.breakevenMonth).toBe(expected.breakevenMonth);
      expect(roi.monthsToBreakeven).toBe(expected.monthsToBreakeven);
      expect(monthFigures(roi, "2026-10")).toMatchObject(monthShape(expected.october));
      if (expected.may) {
        expect(monthFigures(roi, "2026-05")).toMatchObject(monthShape(expected.may));
      }
    }
    expect(rows.reduce((sum, row) => sum + row.roi.cumulativeNet, 0)).toBe(FLEET_CUMULATIVE_NET);
    expect(rows.reduce((sum, row) => sum + row.roi.capital, 0)).toBe(FLEET_CAPITAL);
    expect(
      rows.reduce((sum, row) => sum + (monthFigures(row.roi, "2026-10")?.net ?? 0), 0),
    ).toBe(OCTOBER_NET_TOTAL);
  });

  it("roi_hit_flag", () => {
    const hits = rows.filter((row) => row.roi.roiHit);
    expect(hits.length).toBeGreaterThanOrEqual(1);
    expect(hits.map((row) => row.car.id).sort()).toEqual(["car_harbor", "car_plover"]);
    for (const row of rows) {
      expect(row.roi.roiHit).toBe(row.roi.cumulativeNet >= row.roi.capital);
    }
    expect(rows.find((row) => row.car.id === "car_sable")?.roi.roiHit).toBe(false);
  });

  it("maint_alert_threshold", () => {
    expect(isMaintenanceAlert(250, 1000)).toBe(false);
    expect(isMaintenanceAlert(251, 1000)).toBe(true);
    expect(isMaintenanceAlert(249, 1000)).toBe(false);
    expect(isMaintenanceAlert(0, 1000)).toBe(false);
    expect(isMaintenanceAlert(10, 0)).toBe(false);
    const sable = rows.find((row) => row.car.id === "car_sable")!;
    const quill = rows.find((row) => row.car.id === "car_quill")!;
    const harbor = rows.find((row) => row.car.id === "car_harbor")!;
    expect(monthFigures(sable.roi, "2026-10")?.maintenanceAlert).toBe(true);
    expect(monthFigures(sable.roi, "2026-05")?.maintenanceAlert).toBe(false);
    expect(monthFigures(quill.roi, "2026-10")?.maintenanceAlert).toBe(false);
    expect(monthFigures(harbor.roi, "2026-10")?.maintenanceAlert).toBe(false);
  });
});

function monthShape(month: {
  gross: number;
  income: number;
  maintenance: number;
  other: number;
  net: number;
  alert: boolean;
}) {
  return {
    gross: month.gross,
    income: month.income,
    maintenance: month.maintenance,
    other: month.other,
    net: month.net,
    maintenanceAlert: month.alert,
  };
}
