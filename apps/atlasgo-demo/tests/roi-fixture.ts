export interface MonthExpect {
  gross: number;
  income: number;
  maintenance: number;
  other: number;
  net: number;
  alert: boolean;
}

export interface CarExpect {
  carId: string;
  capital: number;
  cumulativeNet: number;
  roiHit: boolean;
  breakevenMonth: string | null;
  monthsToBreakeven: number | null;
  october: MonthExpect;
  may?: MonthExpect;
}

export const ROI_FIXTURE: CarExpect[] = [
  {
    carId: "car_harbor",
    capital: 4200,
    cumulativeNet: 7200,
    roiHit: true,
    breakevenMonth: "2026-08",
    monthsToBreakeven: 4,
    october: { gross: 1400, income: 1260, maintenance: 40, other: 20, net: 1200, alert: false },
  },
  {
    carId: "car_lumen",
    capital: 5800,
    cumulativeNet: 4440,
    roiHit: false,
    breakevenMonth: null,
    monthsToBreakeven: null,
    october: { gross: 1100, income: 1000, maintenance: 50, other: 30, net: 740, alert: false },
  },
  {
    carId: "car_sable",
    capital: 8000,
    cumulativeNet: 1680,
    roiHit: false,
    breakevenMonth: null,
    monthsToBreakeven: null,
    may: { gross: 700, income: 630, maintenance: 40, other: 20, net: 320, alert: false },
    october: { gross: 600, income: 540, maintenance: 180, other: 30, net: 80, alert: true },
  },
  {
    carId: "car_cedar",
    capital: 5000,
    cumulativeNet: 3750,
    roiHit: false,
    breakevenMonth: null,
    monthsToBreakeven: null,
    october: { gross: 900, income: 810, maintenance: 60, other: 25, net: 625, alert: false },
  },
  {
    carId: "car_nimbus",
    capital: 6400,
    cumulativeNet: 4560,
    roiHit: false,
    breakevenMonth: null,
    monthsToBreakeven: null,
    october: { gross: 1200, income: 1080, maintenance: 80, other: 40, net: 760, alert: false },
  },
  {
    carId: "car_quill",
    capital: 4000,
    cumulativeNet: 2700,
    roiHit: false,
    breakevenMonth: null,
    monthsToBreakeven: null,
    october: { gross: 800, income: 720, maintenance: 200, other: 20, net: 450, alert: false },
  },
  {
    carId: "car_fable",
    capital: 7500,
    cumulativeNet: 5070,
    roiHit: false,
    breakevenMonth: null,
    monthsToBreakeven: null,
    october: { gross: 1300, income: 1170, maintenance: 70, other: 35, net: 845, alert: false },
  },
  {
    carId: "car_plover",
    capital: 4500,
    cumulativeNet: 5400,
    roiHit: true,
    breakevenMonth: "2026-09",
    monthsToBreakeven: 5,
    october: { gross: 1000, income: 950, maintenance: 30, other: 20, net: 900, alert: false },
  },
];

export const FLEET_CUMULATIVE_NET = 34800;
export const FLEET_CAPITAL = 45400;
export const OCTOBER_NET_TOTAL = 5600;
