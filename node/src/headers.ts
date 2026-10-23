/** Read-only, case-insensitive response headers. Values are never comma-split. */
export class Headers implements Iterable<[string, string]> {
  readonly #values = new Map<string, readonly string[]>();

  constructor(values: Record<string, string[]> = {}, fallback: Record<string, string> = {}) {
    for (const [name, value] of Object.entries(fallback)) this.#values.set(name.toLowerCase(), [value]);
    for (const [name, list] of Object.entries(values)) this.#values.set(name.toLowerCase(), [...list]);
  }

  get(name: string, fallback: string): string;
  get(name: string): string | undefined;
  get(name: string, fallback?: string): string | undefined { return this.#values.get(name.toLowerCase())?.[0] ?? fallback; }
  getAll(name: string): string[] { return [...(this.#values.get(name.toLowerCase()) ?? [])]; }
  has(name: string): boolean { return this.#values.has(name.toLowerCase()); }
  *entries(): IterableIterator<[string, string]> {
    for (const [name, values] of this.#values) if (values.length) yield [name, values[0]];
  }
  *multiItems(): IterableIterator<[string, string]> {
    for (const [name, values] of this.#values) for (const value of values) yield [name, value];
  }
  [Symbol.iterator](): IterableIterator<[string, string]> { return this.entries(); }
}
