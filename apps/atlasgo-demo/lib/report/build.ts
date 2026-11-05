import type { Car, Expense, Investor, Platform, Trip } from "@/lib/data/types";
import { PLATFORMS } from "@/lib/data/types";

export interface ReportCarRow {
  carId: string;
  carName: string;
  income: number;
  maintenance: number;
  other: number;
  monthlyPayment: number;
  payout: number;
  net: number;
}

export interface ReportPlatformRow {
  platform: Platform;
  gross: number;
  fees: number;
  income: number;
}

export interface CategoryTotal {
  category: "maintenance" | "other";
  amount: number;
}

export interface MonthlyReport {
  investorId: string;
  investorName: string;
  month: string;
  cars: ReportCarRow[];
  platforms: ReportPlatformRow[];
  expensesByCategory: CategoryTotal[];
  totals: ReportCarRow;
}

export function buildMonthlyReport(input: {
  investor: Pick<Investor, "id" | "name">;
  month: string;
  cars: readonly Car[];
  trips: readonly Trip[];
  expenses: readonly Expense[];
}): MonthlyReport {
  const carIds = new Set(input.cars.map((car) => car.id));
  const trips = input.trips.filter(
    (trip) => carIds.has(trip.carId) && trip.startDate.startsWith(input.month),
  );
  const expenses = input.expenses.filter(
    (expense) => carIds.has(expense.carId) && expense.date.startsWith(input.month),
  );

  const cars = input.cars.map((car) => {
    const carTrips = trips.filter((trip) => trip.carId === car.id);
    const carExpenses = expenses.filter((expense) => expense.carId === car.id);
    const gross = sum(carTrips.map((trip) => trip.gross));
    const fees = sum(carTrips.map((trip) => trip.fees));
    const income = gross - fees;
    const maintenance = sum(
      carExpenses.filter((expense) => expense.category === "maintenance").map((expense) => expense.amount),
    );
    const other = sum(
      carExpenses.filter((expense) => expense.category === "other").map((expense) => expense.amount),
    );
    const payout = income - maintenance - other;
    return {
      carId: car.id,
      carName: car.name,
      income,
      maintenance,
      other,
      monthlyPayment: car.monthlyPayment,
      payout,
      net: payout - car.monthlyPayment,
    };
  });

  const platforms = PLATFORMS.map((platform) => {
    const rows = trips.filter((trip) => trip.platform === platform);
    const gross = sum(rows.map((trip) => trip.gross));
    const fees = sum(rows.map((trip) => trip.fees));
    return { platform, gross, fees, income: gross - fees };
  }).filter((row) => row.gross !== 0 || row.fees !== 0);

  const totals = {
    carId: "total",
    carName: "Total",
    income: sum(cars.map((car) => car.income)),
    maintenance: sum(cars.map((car) => car.maintenance)),
    other: sum(cars.map((car) => car.other)),
    monthlyPayment: sum(cars.map((car) => car.monthlyPayment)),
    payout: sum(cars.map((car) => car.payout)),
    net: sum(cars.map((car) => car.net)),
  };

  return {
    investorId: input.investor.id,
    investorName: input.investor.name,
    month: input.month,
    cars,
    platforms,
    expensesByCategory: [
      { category: "maintenance", amount: totals.maintenance },
      { category: "other", amount: totals.other },
    ],
    totals,
  };
}

function sum(values: number[]): number {
  return values.reduce((total, value) => total + value, 0);
}
