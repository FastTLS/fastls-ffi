import { Cookie as StoredCookie, CookieJar } from "tough-cookie";
import { FastlsError } from "./errors.js";

export interface CookieOptions {
  url: string;
  path?: string;
  domain?: string;
  secure?: boolean;
  httpOnly?: boolean;
  maxAge?: number;
  /** RFC3339 timestamp. */
  expires?: string;
}
export interface Cookie { name: string; value: string; }

export class Cookies {
  private readonly jar = new CookieJar();
  constructor(private readonly ensureOpen: () => void, private readonly enabled = true) {}

  private url(value: string): string {
    try {
      const url = new URL(value.replace(/^ws:/, "http:").replace(/^wss:/, "https:"));
      if (!["http:", "https:"].includes(url.protocol) || url.username || url.password) throw new Error();
      return url.href;
    } catch { throw new FastlsError("cookie URL must be absolute HTTP(S) without credentials"); }
  }

  getAll(url: string): Cookie[] {
    this.ensureOpen();
    const target = this.url(url);
    return this.enabled ? this.jar.getCookiesSync(target).map(cookie => ({ name: cookie.key, value: cookie.value })) : [];
  }
  get(name: string, url: string): string | undefined {
    return this.getAll(url).find(cookie => cookie.name === name)?.value;
  }
  set(name: string, value: string, options: CookieOptions): void {
    this.ensureOpen();
    if (!this.enabled) throw new FastlsError("cookie store is disabled");
    if (!/^[!#$%&'*+.^_`|~0-9A-Za-z-]+$/.test(name) || /[^\x20-\x7e]|[;"\\]/.test(value) || Buffer.byteLength(name + value) > 4096) throw new FastlsError("invalid cookie name or value");
    if (options.path !== undefined && (!options.path.startsWith("/") || /[\x00-\x1f;]/.test(options.path))) throw new FastlsError("invalid cookie path");
    if (options.maxAge !== undefined && (!Number.isInteger(options.maxAge) || options.maxAge < -2147483648 || options.maxAge > 2147483647)) throw new FastlsError("invalid cookie maxAge");
    const expires = options.expires === undefined ? undefined : new Date(options.expires);
    if (expires && (!Number.isFinite(expires.getTime()) || !/^\d{4}-\d\d-\d\dT.*(?:Z|[+-]\d\d:\d\d)$/.test(options.expires!))) throw new FastlsError("expires must use RFC3339 format");
    const cookie = new StoredCookie({ key: name, value, path: options.path, domain: options.domain?.replace(/^\./, ""), secure: options.secure, httpOnly: options.httpOnly, maxAge: options.maxAge, expires });
    try { this.jar.setCookieSync(cookie, this.url(options.url)); }
    catch { throw new FastlsError("invalid cookie domain or attributes"); }
  }
  delete(name: string, options: Pick<CookieOptions, "url" | "path" | "domain">): void {
    this.set(name, "", { ...options, maxAge: 0 });
  }
  clear(): void { this.ensureOpen(); this.jar.removeAllCookiesSync(); }
  /** @internal */
  header(url: string): string { return this.getAll(url).map(cookie => `${cookie.name}=${cookie.value}`).join("; "); }
  /** @internal */
  extract(url: string, fields: string[]): void {
    this.ensureOpen();
    if (!this.enabled) return;
    const target = this.url(url);
    for (const field of fields) this.jar.setCookieSync(field, target, { ignoreError: true });
  }
}
