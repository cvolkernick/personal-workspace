export const PLATFORMS = ["Turo", "Upcar", "Direct", "Uber Fleet"] as const;
export type Platform = (typeof PLATFORMS)[number];

export const CAR_STATUSES = [
  "Available",
  "Rented",
  "Reserved",
  "Maintenance",
  "Out of service",
] as const;
export type CarStatusKind = (typeof CAR_STATUSES)[number];

export const EXPENSE_CATEGORIES = ["maintenance", "other"] as const;
export type ExpenseCategory = (typeof EXPENSE_CATEGORIES)[number];

export type ExpenseSource = "seed" | "invoice";
export type InvoiceReviewState = "pending" | "approved";

export interface Investor {
  id: string;
  name: string;
  email: string;
}

export interface Car {
  id: string;
  investorId: string;
  name: string;
  year: number;
  make: string;
  model: string;
  plate: string;
  vin: string;
  purchaseCost: number;
  downPayment: number;
  monthlyPayment: number;
}

export interface Trip {
  id: string;
  carId: string;
  platform: Platform;
  startDate: string;
  endDate: string;
  gross: number;
  fees: number;
}

export interface Expense {
  id: string;
  carId: string;
  category: ExpenseCategory;
  amount: number;
  date: string;
  source: ExpenseSource;
  invoiceId?: string;
}

export interface InvoiceExtraction {
  vendor: string;
  date: string;
  amount: number;
  suggestedCarId: string | null;
  rawText: string;
}

export interface Invoice {
  id: string;
  imageRef: string;
  extracted: InvoiceExtraction;
  reviewState: InvoiceReviewState;
  carId: string | null;
  category: ExpenseCategory | null;
  amount: number | null;
  ownerId: string;
  approvedExpenseId?: string;
}

export interface InvoiceImage {
  mediaType: string;
  bytesBase64: string;
}

export interface CarStatus {
  carId: string;
  status: CarStatusKind;
  renter: string | null;
  start: string | null;
  expectedReturn: string | null;
}

export interface DateRange {
  from: string;
  to: string;
}

export interface CreateInvoiceInput {
  fileName: string;
  mediaType: string;
  bytesBase64: string;
  ownerId: string;
}

export interface ApproveInvoiceInput {
  carId: string;
  category: ExpenseCategory;
  amount: number;
  date: string;
  vendor: string;
}

export interface DataSource {
  listInvestors(): Promise<Investor[]>;
  listCars(investorId?: string): Promise<Car[]>;
  getCar(carId: string): Promise<Car | null>;
  listTrips(carId: string, range?: DateRange): Promise<Trip[]>;
  listExpenses(carId: string, range?: DateRange): Promise<Expense[]>;
  listInvoices(): Promise<Invoice[]>;
  getInvoice(invoiceId: string): Promise<Invoice | null>;
  getInvoiceImage(invoiceId: string): Promise<InvoiceImage | null>;
  createInvoice(input: CreateInvoiceInput): Promise<Invoice>;
  approveInvoice(invoiceId: string, review: ApproveInvoiceInput): Promise<Invoice>;
  getStatusBoard(): Promise<CarStatus[]>;
}
