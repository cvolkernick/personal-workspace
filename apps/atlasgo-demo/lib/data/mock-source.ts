import { expandSeed, type SeedFile } from "./expand-seed";
import { FleetRepository } from "./fleet-repository";
import { loadSeedFile } from "./load-seed";

export class MockDataSource extends FleetRepository {
  constructor(seed?: SeedFile) {
    super(expandSeed(seed ?? loadSeedFile()));
  }
}
