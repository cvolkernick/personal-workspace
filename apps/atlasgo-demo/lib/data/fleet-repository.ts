import { clone } from "./clone";
import { inRange } from "./dates";
import { mockExtract } from "./extract";
import type { ExpandedSeed } from "./expand-seed";
import type {
  ApproveInvoiceInput,
  Car,
  CarStatus,
  CreateInvoiceInput,
  DataSource,
  DateRange,
  Expense,
  ExpenseCategory,
  Investor,
  Invoice,
  InvoiceImage,
  Trip,
} from "./types";

export class FleetRepository implements DataSource {
  private investors: Investor[];
  private cars: Car[];
  private trips: Trip[];
  private expenses: Expense[];
  private statuses: CarStatus[];
  private invoices: Invoice[] = [];
  private images = new Map<string, InvoiceImage>();
  private invoiceSeq = 1;

  constructor(data: ExpandedSeed) {
    this.investors = clone(data.investors);
    this.cars = clone(data.cars);
    this.trips = clone(data.trips);
    this.expenses = clone(data.expenses);
    this.statuses = clone(data.statuses);
  }

  async listInvestors(): Promise<Investor[]> {
    return clone(this.investors);
  }

  async listCars(investorId?: string): Promise<Car[]> {
    const cars = investorId ? this.cars.filter((car) => car.investorId === investorId) : this.cars;
    return clone(cars);
  }

  async getCar(carId: string): Promise<Car | null> {
    const car = this.cars.find((item) => item.id === carId);
    return car ? clone(car) : null;
  }

  async listTrips(carId: string, range?: DateRange): Promise<Trip[]> {
    return clone(
      this.trips.filter((trip) => trip.carId === carId && inRange(trip.startDate, range)),
    );
  }

  async listExpenses(carId: string, range?: DateRange): Promise<Expense[]> {
    return clone(
      this.expenses.filter((expense) => expense.carId === carId && inRange(expense.date, range)),
    );
  }

  async listInvoices(): Promise<Invoice[]> {
    return clone(this.invoices);
  }

  async getInvoice(invoiceId: string): Promise<Invoice | null> {
    const invoice = this.invoices.find((item) => item.id === invoiceId);
    return invoice ? clone(invoice) : null;
  }

  async getInvoiceImage(invoiceId: string): Promise<InvoiceImage | null> {
    const image = this.images.get(invoiceId);
    return image ? clone(image) : null;
  }

  async createInvoice(input: CreateInvoiceInput): Promise<Invoice> {
    const id = `invoice_${this.invoiceSeq++}`;
    const invoice: Invoice = {
      id,
      imageRef: `mem://${id}`,
      extracted: mockExtract(input.fileName, this.cars),
      reviewState: "pending",
      carId: null,
      category: null,
      amount: null,
      ownerId: input.ownerId,
    };
    this.invoices.push(invoice);
    this.images.set(id, { mediaType: input.mediaType, bytesBase64: input.bytesBase64 });
    return clone(invoice);
  }

  async approveInvoice(invoiceId: string, review: ApproveInvoiceInput): Promise<Invoice> {
    const invoice = this.invoices.find((item) => item.id === invoiceId);
    if (!invoice) throw new Error("Invoice not found");
    if (invoice.reviewState !== "pending") throw new Error("Invoice is not pending review");
    const car = this.cars.find((item) => item.id === review.carId);
    if (!car) throw new Error("Car not found");
    if (!isCategory(review.category)) throw new Error("Invalid category");
    if (!Number.isFinite(review.amount) || review.amount < 0) throw new Error("Invalid amount");
    if (!/^\d{4}-\d{2}-\d{2}$/.test(review.date)) throw new Error("Invalid date");

    const expenseId = `exp_${invoice.id}`;
    this.expenses.push({
      id: expenseId,
      carId: review.carId,
      category: review.category,
      amount: review.amount,
      date: review.date,
      source: "invoice",
      invoiceId: invoice.id,
    });
    invoice.reviewState = "approved";
    invoice.carId = review.carId;
    invoice.category = review.category;
    invoice.amount = review.amount;
    invoice.approvedExpenseId = expenseId;
    invoice.extracted = {
      ...invoice.extracted,
      vendor: review.vendor,
      date: review.date,
      amount: review.amount,
    };
    return clone(invoice);
  }

  async getStatusBoard(): Promise<CarStatus[]> {
    return clone(this.statuses);
  }
}

function isCategory(value: string): value is ExpenseCategory {
  return value === "maintenance" || value === "other";
}
