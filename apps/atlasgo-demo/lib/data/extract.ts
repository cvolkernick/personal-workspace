import type { Car, InvoiceExtraction } from "./types";

export function mockExtract(fileName: string, cars: readonly Car[]): InvoiceExtraction {
  const haystack = fileName.toUpperCase();
  const match = cars.find(
    (car) => haystack.includes(car.plate.toUpperCase()) || haystack.includes(car.vin.toUpperCase()),
  );
  const vendor = "Northline Care";
  const date = "2026-10-21";
  const amount = 125;
  return {
    vendor,
    date,
    amount,
    suggestedCarId: match?.id ?? null,
    rawText: `${vendor}\n${date}\n${amount}\n${fileName}`,
  };
}
