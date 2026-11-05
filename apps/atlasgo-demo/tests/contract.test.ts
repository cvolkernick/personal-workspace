import { expect, it } from "vitest";
import { AltMemorySource } from "@/lib/data/alt-source";
import { createDataSource } from "@/lib/data";
import { NotImplementedError } from "@/lib/data/errors";
import type { SeedFile } from "@/lib/data/expand-seed";
import { loadSeedFile } from "@/lib/data/load-seed";
import { LiveDataSource } from "@/lib/data/live-source";
import { MockDataSource } from "@/lib/data/mock-source";
import type { DataSource } from "@/lib/data/types";
import { HISTORY_MONTHS, buildCarRoi, monthFigures } from "@/lib/roi/calc";
import { ROI_FIXTURE } from "./roi-fixture";

it("both_sources_pass", async () => {
  await expectWorkingSource(new MockDataSource());
  await expectWorkingSource(new AltMemorySource(loadSeedFile()));
  await expectWorkingSource(createDataSource("mock"));
  await expectWorkingSource(createDataSource("memory"));
  await expectLiveStub(new LiveDataSource());
  await expectLiveStub(createDataSource("live"));
  await expectInvoiceGate(new MockDataSource(tinySeed()));
  await expectInvoiceGate(new AltMemorySource(tinySeed()));

  const mock = new MockDataSource();
  const alt = new AltMemorySource(loadSeedFile());
  expect(mock).toBeInstanceOf(MockDataSource);
  expect(alt).toBeInstanceOf(AltMemorySource);
  expect(alt).not.toBeInstanceOf(MockDataSource);
  expect(new LiveDataSource()).toBeInstanceOf(LiveDataSource);
  expect(await mock.listTrips("car_harbor")).toEqual(await alt.listTrips("car_harbor"));
  expect(await mock.getStatusBoard()).toEqual(await alt.getStatusBoard());
});

async function expectWorkingSource(source: DataSource) {
  const cars = await source.listCars();
  expect(cars).toHaveLength(8);
  expect(await source.listCars("inv_avery")).toHaveLength(3);
  expect(await source.listCars("missing")).toEqual([]);
  expect(await source.getCar("nope")).toBeNull();
  const bundles = await Promise.all(
    cars.map(async (car) => ({
      car,
      roi: buildCarRoi(car, await source.listTrips(car.id), await source.listExpenses(car.id), HISTORY_MONTHS),
    })),
  );
  for (const expected of ROI_FIXTURE) {
    const roi = bundles.find((item) => item.car.id === expected.carId)?.roi;
    expect(roi?.cumulativeNet).toBe(expected.cumulativeNet);
    expect(roi?.roiHit).toBe(expected.roiHit);
    expect(roi?.monthsToBreakeven).toBe(expected.monthsToBreakeven);
    expect(monthFigures(roi!, "2026-10")?.maintenanceAlert).toBe(expected.october.alert);
  }
  expect(await source.getStatusBoard()).toHaveLength(8);
  const outside = await source.listTrips("car_harbor", { from: "2026-01-01", to: "2026-01-31" });
  expect(outside).toEqual([]);
}

async function expectLiveStub(source: DataSource) {
  const calls: Array<Promise<unknown>> = [
    source.listInvestors(),
    source.listCars(),
    source.listCars("inv_avery"),
    source.getCar("car_harbor"),
    source.listTrips("car_harbor"),
    source.listTrips("car_harbor", { from: "2026-05-01", to: "2026-05-31" }),
    source.listExpenses("car_harbor"),
    source.listInvoices(),
    source.getInvoice("invoice_1"),
    source.getInvoiceImage("invoice_1"),
    source.createInvoice({ fileName: "a.png", mediaType: "image/png", bytesBase64: "aa", ownerId: "inv_avery" }),
    source.approveInvoice("invoice_1", {
      carId: "car_harbor",
      category: "maintenance",
      amount: 1,
      date: "2026-10-21",
      vendor: "Northline Care",
    }),
    source.getStatusBoard(),
  ];
  for (const call of calls) {
    await expect(call).rejects.toBeInstanceOf(NotImplementedError);
  }
}

async function expectInvoiceGate(source: DataSource) {
  const before = await source.listExpenses("car_sample");
  expect(before).toHaveLength(2);
  const created = await source.createInvoice({
    fileName: "note-DEMO-Z9.png",
    mediaType: "image/png",
    bytesBase64: "aGVsbG8=",
    ownerId: "inv_sample",
  });
  expect(created.reviewState).toBe("pending");
  expect(created.carId).toBeNull();
  expect(created.extracted.suggestedCarId).toBe("car_sample");
  expect(await source.listExpenses("car_sample")).toHaveLength(2);
  const image = await source.getInvoiceImage(created.id);
  expect(image?.bytesBase64).toBe("aGVsbG8=");

  const approved = await source.approveInvoice(created.id, {
    carId: "car_sample",
    category: "maintenance",
    amount: 80,
    date: "2026-10-21",
    vendor: "Northline Care",
  });
  expect(approved.reviewState).toBe("approved");
  const after = await source.listExpenses("car_sample");
  expect(after).toHaveLength(3);
  expect(after.filter((expense) => expense.source === "invoice").map((expense) => expense.amount)).toEqual([80]);
  await expect(
    source.approveInvoice(created.id, {
      carId: "car_sample",
      category: "maintenance",
      amount: 80,
      date: "2026-10-21",
      vendor: "Northline Care",
    }),
  ).rejects.toThrow(/pending/);
  expect(await source.listExpenses("car_sample")).toHaveLength(3);
}

function tinySeed(): SeedFile {
  return {
    investors: [{ id: "inv_sample", name: "Sample Owner", email: "sample.owner@example.com" }],
    cars: [
      {
        id: "car_sample",
        investorId: "inv_sample",
        name: "Sample",
        year: 2020,
        make: "Sample",
        model: "Car",
        plate: "DEMO-Z9",
        vin: "DEMO0ATLASVIN0099",
        purchaseCost: 4000,
        downPayment: 0,
        monthlyPayment: 0,
      },
    ],
    statuses: [
      { carId: "car_sample", status: "Available", renter: null, start: "2026-10-01", expectedReturn: null },
    ],
    months: ["2026-10"],
    schedules: [
      { carId: "car_sample", platform: "Direct", gross: 1000, fees: 100, maintenance: 50, other: 25 },
    ],
  };
}
