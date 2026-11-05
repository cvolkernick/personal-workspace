import type { Car, CarStatus, Expense, Investor, Trip } from "./types";

export interface ScheduleOverride {
  platform?: Trip["platform"];
  gross?: number;
  fees?: number;
  maintenance?: number;
  other?: number;
}

export interface Schedule {
  carId: string;
  platform: Trip["platform"];
  gross: number;
  fees: number;
  maintenance: number;
  other: number;
  overrides?: Record<string, ScheduleOverride>;
}

export interface SeedFile {
  investors: Investor[];
  cars: Car[];
  statuses: CarStatus[];
  months: string[];
  schedules: Schedule[];
}

export interface ExpandedSeed {
  investors: Investor[];
  cars: Car[];
  trips: Trip[];
  expenses: Expense[];
  statuses: CarStatus[];
}

export function expandSeed(seed: SeedFile): ExpandedSeed {
  const trips: Trip[] = [];
  const expenses: Expense[] = [];
  for (const schedule of seed.schedules) {
    for (const month of seed.months) {
      const override = schedule.overrides?.[month] ?? {};
      const idPart = `${schedule.carId}_${month}`;
      trips.push({
        id: `trip_${idPart}`,
        carId: schedule.carId,
        platform: override.platform ?? schedule.platform,
        startDate: `${month}-03`,
        endDate: `${month}-06`,
        gross: override.gross ?? schedule.gross,
        fees: override.fees ?? schedule.fees,
      });
      expenses.push({
        id: `exp_${idPart}_maint`,
        carId: schedule.carId,
        category: "maintenance",
        amount: override.maintenance ?? schedule.maintenance,
        date: `${month}-12`,
        source: "seed",
      });
      expenses.push({
        id: `exp_${idPart}_other`,
        carId: schedule.carId,
        category: "other",
        amount: override.other ?? schedule.other,
        date: `${month}-18`,
        source: "seed",
      });
    }
  }
  return {
    investors: seed.investors,
    cars: seed.cars,
    trips,
    expenses,
    statuses: seed.statuses,
  };
}
