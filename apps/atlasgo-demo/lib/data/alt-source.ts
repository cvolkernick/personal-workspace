import type {
  ApproveInvoiceInput,
  Car,
  CarStatus,
  CreateInvoiceInput,
  DataSource,
  DateRange,
  Expense,
  Investor,
  Invoice,
  InvoiceImage,
  Trip,
} from "./types";
import { expandSeed, type SeedFile } from "./expand-seed";
import { FleetRepository } from "./fleet-repository";

/**
 * Alternate in-memory adapter. It is a distinct DataSource class from
 * MockDataSource so the app can select it with DATA_SOURCE=memory and the
 * contract suite can run the same checks against both.
 */
export class AltMemorySource implements DataSource {
  private readonly inner: FleetRepository;

  constructor(seed: SeedFile) {
    this.inner = new FleetRepository(expandSeed(structuredClone(seed)));
  }

  listInvestors(): Promise<Investor[]> {
    return this.inner.listInvestors();
  }

  listCars(investorId?: string): Promise<Car[]> {
    return this.inner.listCars(investorId);
  }

  getCar(carId: string): Promise<Car | null> {
    return this.inner.getCar(carId);
  }

  listTrips(carId: string, range?: DateRange): Promise<Trip[]> {
    return this.inner.listTrips(carId, range);
  }

  listExpenses(carId: string, range?: DateRange): Promise<Expense[]> {
    return this.inner.listExpenses(carId, range);
  }

  listInvoices(): Promise<Invoice[]> {
    return this.inner.listInvoices();
  }

  getInvoice(invoiceId: string): Promise<Invoice | null> {
    return this.inner.getInvoice(invoiceId);
  }

  getInvoiceImage(invoiceId: string): Promise<InvoiceImage | null> {
    return this.inner.getInvoiceImage(invoiceId);
  }

  createInvoice(input: CreateInvoiceInput): Promise<Invoice> {
    return this.inner.createInvoice(input);
  }

  approveInvoice(invoiceId: string, review: ApproveInvoiceInput): Promise<Invoice> {
    return this.inner.approveInvoice(invoiceId, review);
  }

  getStatusBoard(): Promise<CarStatus[]> {
    return this.inner.getStatusBoard();
  }
}
