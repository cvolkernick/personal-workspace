import { expandSeed, type SeedFile } from "./expand-seed";
import { FleetRepository } from "./fleet-repository";
import { loadSeedFile } from "./load-seed";

export class MockDataSource extends FleetRepository {
  constructor(seed?: SeedFile) {
    const data = expandSeed(seed ?? loadSeedFile());
    super(data);
    // Seed one pending sample invoice so the demo always has an invoice to open.
    // On Cloudflare Workers each isolate has its own in-memory store, so uploads
    // made in one request are not guaranteed to be visible in the next.
    const owner = data.investors[0];
    if (owner) {
      void this.createInvoice({
        fileName: "sample-oil-change.png",
        mediaType: "image/png",
        bytesBase64: SAMPLE_PNG,
        ownerId: owner.id,
      });
    }
  }
}

const SAMPLE_PNG =
  "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mP8z8BQDwAEhQGAhKmMIQAAAABJRU5ErkJggg==";
