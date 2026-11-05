import { NotImplementedError } from "./errors";
import type { Car, CarStatus, DataSource, Expense, Investor, Invoice, InvoiceImage, Trip } from "./types";

function unimplemented(method: string): Promise<never> {
  return Promise.reject(new NotImplementedError(method));
}

export class LiveDataSource implements DataSource {
  listInvestors(): Promise<Investor[]> {
    return unimplemented("listInvestors");
  }

  listCars(): Promise<Car[]> {
    return unimplemented("listCars");
  }

  getCar(): Promise<Car | null> {
    return unimplemented("getCar");
  }

  listTrips(): Promise<Trip[]> {
    return unimplemented("listTrips");
  }

  listExpenses(): Promise<Expense[]> {
    return unimplemented("listExpenses");
  }

  listInvoices(): Promise<Invoice[]> {
    return unimplemented("listInvoices");
  }

  getInvoice(): Promise<Invoice | null> {
    return unimplemented("getInvoice");
  }

  getInvoiceImage(): Promise<InvoiceImage | null> {
    return unimplemented("getInvoiceImage");
  }

  createInvoice(): Promise<Invoice> {
    return unimplemented("createInvoice");
  }

  approveInvoice(): Promise<Invoice> {
    return unimplemented("approveInvoice");
  }

  getStatusBoard(): Promise<CarStatus[]> {
    return unimplemented("getStatusBoard");
  }
}
