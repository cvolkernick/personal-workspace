import type { Car, Expense, Trip } from "@/lib/data/types";

export const MAINTENANCE_ALERT_THRESHOLD = 0.25;

export const HISTORY_MONTHS = [
  "2026-05",
  "2026-06",
  "2026-07",
  "2026-08",
  "2026-09",
  "2026-10",
] as const;

export interface MonthFigures {
  month: string;
  gross: number;
  fees: number;
  income: number;
  maintenance: number;
  other: number;
  monthlyPayment: number;
  net: number;
  maintenanceAlert: boolean;
}

export interface CarRoi {
  carId: string;
  capital: number;
  months: MonthFigures[];
  cumulativeNet: number;
  roiRatio: number;
  roiHit: boolean;
  breakevenMonth: string | null;
  monthsToBreakeven: number | null;
}

export function capitalInvested(car: Pick<Car, "purchaseCost" | "downPayment">): number {
  return car.purchaseCost + car.downPayment;
}

export function isMaintenanceAlert(
  maintenance: number,
  gross: number,
  threshold = MAINTENANCE_ALERT_THRESHOLD,
): boolean {
  if (gross <= 0) return false;
  return maintenance / gross > threshold;
}

export function buildCarRoi(
  car: Car,
  trips: readonly Trip[],
  expenses: readonly Expense[],
  months: readonly string[],
): CarRoi {
  const capital = capitalInvested(car);
  const monthRows: MonthFigures[] = [];
  let cumulative = 0;
  let breakevenMonth: string | null = null;
  let monthsToBreakeven: number | null = null;

  months.forEach((month, index) => {
    const monthTrips = trips.filter((trip) => trip.carId === car.id && trip.startDate.startsWith(month));
    const monthExpenses = expenses.filter(
      (expense) => expense.carId === car.id && expense.date.startsWith(month),
    );
    const gross = sum(monthTrips.map((trip) => trip.gross));
    const fees = sum(monthTrips.map((trip) => trip.fees));
    const income = gross - fees;
    const maintenance = sum(
      monthExpenses.filter((expense) => expense.category === "maintenance").map((expense) => expense.amount),
    );
    const other = sum(
      monthExpenses.filter((expense) => expense.category === "other").map((expense) => expense.amount),
    );
    const net = income - maintenance - other - car.monthlyPayment;
    cumulative += net;
    if (monthsToBreakeven === null && cumulative >= capital) {
      monthsToBreakeven = index + 1;
      breakevenMonth = month;
    }
    monthRows.push({
      month,
      gross,
      fees,
      income,
      maintenance,
      other,
      monthlyPayment: car.monthlyPayment,
      net,
      maintenanceAlert: isMaintenanceAlert(maintenance, gross),
    });
  });

  return {
    carId: car.id,
    capital,
    months: monthRows,
    cumulativeNet: cumulative,
    roiRatio: capital === 0 ? 0 : cumulative / capital,
    roiHit: cumulative >= capital,
    breakevenMonth,
    monthsToBreakeven,
  };
}

export function monthFigures(roi: CarRoi, month: string): MonthFigures | undefined {
  return roi.months.find((item) => item.month === month);
}

export function selectedMonth(value: string | undefined): string {
  if (value && (HISTORY_MONTHS as readonly string[]).includes(value)) return value;
  return "2026-10";
}

function sum(values: number[]): number {
  return values.reduce((total, value) => total + value, 0);
}
