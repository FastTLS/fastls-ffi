export class FastlsError extends Error {
  override name = "FastlsError";
}

export type TimeoutPhase = "connect" | "write" | "headers" | "read" | "total";
export class FastlsTimeoutError extends FastlsError {
  override name = "FastlsTimeoutError";
  constructor(message: string, readonly phase: TimeoutPhase) { super(message); }
}
export class FastlsDecodeError extends FastlsError { override name = "FastlsDecodeError"; }
export function responseError(info: { error?: string; error_code?: string; timeout_phase?: string }): FastlsError {
  const message = info.error ?? "native request failed";
  if (info.error_code === "timeout") return new FastlsTimeoutError(message, (info.timeout_phase ?? "total") as TimeoutPhase);
  if (info.error_code === "decode_error") return new FastlsDecodeError(message);
  return new FastlsError(message);
}

export class AbortError extends FastlsError {
  override name = "AbortError";
  readonly code = "ABORT_ERR";
  constructor(message = "The request was aborted", options?: ErrorOptions) {
    super(message, options);
  }
}
