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
  constructor(private readonly command: (input: unknown) => unknown) {}

  getAll(url: string): Cookie[] {
    return this.command({ action: "get", url }) as Cookie[];
  }
  get(name: string, url: string): string | undefined {
    return this.getAll(url).find(cookie => cookie.name === name)?.value;
  }
  set(name: string, value: string, options: CookieOptions): void {
    this.command({ action: "set", url: options.url, cookie: {
      name, value, path: options.path, domain: options.domain,
      secure: options.secure, http_only: options.httpOnly,
      max_age: options.maxAge, expires: options.expires,
    } });
  }
  delete(name: string, options: Pick<CookieOptions, "url" | "path" | "domain">): void {
    this.set(name, "", { ...options, maxAge: 0 });
  }
  clear(): void { this.command({ action: "clear" }); }
}
