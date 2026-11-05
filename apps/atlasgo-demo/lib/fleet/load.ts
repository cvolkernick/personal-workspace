import type { DataSource } from "@/lib/data/types";
import { buildCarRoi, HISTORY_MONTHS, type CarRoi } from "@/lib/roi/calc";
import { buildMonthlyReport, type MonthlyReport } from "@/lib/report/build";
import type { Car, Expense, Trip } from "@/lib/data/types";

export interface DashboardRow {
  car: Car;
  roi: CarRoi;
  trips: Trip[];
  expenses: Expense[];
}

export async function loadDashboard(source: DataSource, investorId?: string): Promise<DashboardRow[]> {
  const cars = await source.listCars(investorId);
  return Promise.all(
    cars.map(async (car) => {
      const [trips, expenses] = await Promise.all([source.listTrips(car.id), source.listExpenses(car.id)]);
      return { car, trips, expenses, roi: buildCarRoi(car, trips, expenses, HISTORY_MONTHS) };
    }),
  );
}

export async function loadMonthlyReport(
  source: DataSource,
  investorId: string,
  month: string,
): Promise<MonthlyReport | null> {
  const [investors, cars] = await Promise.all([source.listInvestors(), source.listCars(investorId)]);
  const investor = investors.find((item) => item.id === investorId);
  if (!investor) return null;
  const bundles = await Promise.all(
    cars.map(async (car) => {
      const [trips, expenses] = await Promise.all([source.listTrips(car.id), source.listExpenses(car.id)]);
      return { trips, expenses };
    }),
  );
  return buildMonthlyReport({
    investor,
    month,
    cars,
    trips: bundles.flatMap((bundle) => bundle.trips),
    expenses: bundles.flatMap((bundle) => bundle.expenses),
  });
}
