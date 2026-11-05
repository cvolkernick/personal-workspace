import type { Car, CarStatus, CarStatusKind, Investor } from "@/lib/data/types";
import { daysBetween } from "@/lib/data/dates";

const IDLE_STATUSES = new Set<CarStatusKind>(["Available", "Maintenance", "Out of service"]);

export interface BoardEntry {
  carId: string;
  carName: string;
  investorId: string;
  investorName: string;
  plate: string;
  status: CarStatusKind;
  renter: string | null;
  start: string | null;
  expectedReturn: string | null;
  dueBackToday: boolean;
  overdue: boolean;
  idleDays: number | null;
}

export function buildBoard(input: {
  statuses: readonly CarStatus[];
  cars: readonly Car[];
  investors: readonly Investor[];
  asOf: string;
}): BoardEntry[] {
  return input.statuses.map((status) => {
    const car = input.cars.find((item) => item.id === status.carId);
    const investor = input.investors.find((item) => item.id === car?.investorId);
    const expected = status.expectedReturn;
    const idleDays =
      car && IDLE_STATUSES.has(status.status) && status.start
        ? Math.max(0, daysBetween(status.start, input.asOf))
        : null;
    return {
      carId: status.carId,
      carName: car?.name ?? status.carId,
      investorId: car?.investorId ?? "",
      investorName: investor?.name ?? "",
      plate: car?.plate ?? "",
      status: status.status,
      renter: status.renter,
      start: status.start,
      expectedReturn: expected,
      dueBackToday: expected === input.asOf,
      overdue: !!expected && expected < input.asOf,
      idleDays,
    };
  });
}
