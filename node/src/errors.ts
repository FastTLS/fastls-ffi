export class FastlsError extends Error {
  override name = "FastlsError";
  code?: string | number;
}
export class FastlsHTTPError extends FastlsError {
  override name = "FastlsHTTPError";
  readonly status: number;
  constructor(readonly response: import("./response.js").Response | import("./response.js").StreamResponse) {
    super(`HTTP ${response.status}`);
    this.status = response.status;
  }
}

export type TimeoutPhase = "connect" | "write" | "headers" | "read" | "total" | "handshake";
export class FastlsTimeoutError extends FastlsError {
  override name = "FastlsTimeoutError";
  constructor(message: string, readonly phase: TimeoutPhase) { super(message); }
}
export class FastlsDecodeError extends FastlsError { override name = "FastlsDecodeError"; }
export function responseError(info: { error?: string; error_code?: string; timeout_phase?: string }): FastlsError {
  const message = info.error ?? "native request failed";
  if (info.error_code === "timeout") return new FastlsTimeoutError(message, (info.timeout_phase ?? "total") as TimeoutPhase);
  if (info.error_code === "decode_error") return new FastlsDecodeError(message);
  const error = new FastlsError(message);
  error.code = info.error_code;
  return error;
}

export class AbortError extends FastlsError {
  override name = "AbortError";
  override readonly code = "ABORT_ERR";
  constructor(message = "The request was aborted", options?: ErrorOptions) {
    super(message, options);
  }
}
