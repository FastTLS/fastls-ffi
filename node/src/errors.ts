export class FastlsError extends Error {
  override name = "FastlsError";
}

export class AbortError extends FastlsError {
  override name = "AbortError";
  readonly code = "ABORT_ERR";
  constructor(message = "The request was aborted", options?: ErrorOptions) {
    super(message, options);
  }
}
