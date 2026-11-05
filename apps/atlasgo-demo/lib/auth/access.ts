import type { Actor } from "./actors";
import type { Car, Invoice } from "@/lib/data/types";

export function canSeeCar(actor: Actor, car: Car): boolean {
  return actor.role === "admin" || car.investorId === actor.investorId;
}

export function canSeeInvoice(actor: Actor, invoice: Invoice, cars: readonly Car[]): boolean {
  if (actor.role === "admin") return true;
  if (invoice.ownerId === actor.id) return true;
  const carId = invoice.carId ?? invoice.extracted.suggestedCarId;
  if (!carId) return false;
  return cars.some((car) => car.id === carId && car.investorId === actor.investorId);
}
