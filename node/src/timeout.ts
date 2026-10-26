export interface TimeoutPolicy {
  total: number;
  connect?: number;
  write?: number;
  headers?: number;
  read?: number;
}
export type Timeout = number | Readonly<TimeoutPolicy>;
export function timeoutFields(value: Timeout): Record<string, number> {
  const policy = typeof value === "number" ? { total: value } : value;
  if (!policy || typeof policy !== "object") throw new TypeError("timeoutMs must be milliseconds or a timeout policy");
  const fields: Record<string, number> = {};
  for (const phase of ["total", "connect", "write", "headers", "read"] as const) {
    const number = policy[phase];
    if (number === undefined && phase !== "total") continue;
    if (!Number.isSafeInteger(number) || number! < 1 || number! > 86400000) {
      throw new RangeError(`${phase} timeoutMs must be an integer in 1..86400000`);
    }
    fields[phase === "total" ? "timeout_ms" : `${phase}_timeout_ms`] = number!;
  }
  return fields;
}
