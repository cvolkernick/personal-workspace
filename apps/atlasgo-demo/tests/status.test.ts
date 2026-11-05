import { expect, it } from "vitest";
import { MockDataSource } from "@/lib/data/mock-source";
import { CAR_STATUSES } from "@/lib/data/types";
import { buildBoard } from "@/lib/status/board";

it("flags overdue and due today against the demo clock", async () => {
  const source = new MockDataSource();
  const [statuses, cars, investors] = await Promise.all([
    source.getStatusBoard(),
    source.listCars(),
    source.listInvestors(),
  ]);
  const board = buildBoard({ statuses, cars, investors, asOf: "2026-11-05" });
  expect(new Set(board.map((row) => row.status))).toEqual(new Set(CAR_STATUSES));
  const harbor = board.find((row) => row.carId === "car_harbor")!;
  const quill = board.find((row) => row.carId === "car_quill")!;
  const plover = board.find((row) => row.carId === "car_plover")!;
  const cedar = board.find((row) => row.carId === "car_cedar")!;
  expect(harbor.overdue).toBe(true);
  expect(harbor.dueBackToday).toBe(false);
  expect(harbor.idleDays).toBeNull();
  expect(quill.dueBackToday).toBe(true);
  expect(quill.overdue).toBe(false);
  expect(plover.overdue).toBe(false);
  expect(plover.dueBackToday).toBe(false);
  expect(cedar.idleDays).toBe(18);
  expect(board.find((row) => row.carId === "car_nimbus")?.idleDays).toBe(65);
});
