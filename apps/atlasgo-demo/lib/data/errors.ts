export class NotImplementedError extends Error {
  readonly code = "NOT_IMPLEMENTED" as const;

  constructor(method: string) {
    super(`LiveDataSource.${method} is not implemented`);
    this.name = "NotImplementedError";
  }
}
