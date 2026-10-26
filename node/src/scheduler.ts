type Probe = () => boolean;

/** One bounded timer queue for all pending header/read operations in this SDK. */
class Scheduler {
  private pending = new Set<Probe>();
  private timer?: ReturnType<typeof setTimeout>;
  private immediateBudget = 32;
  private resetScheduled = false;

  wait<T>(probe: () => T | undefined, signal: AbortSignal): Promise<T> {
    return new Promise((resolve, reject) => {
      const cleanup = () => {
        this.pending.delete(check);
        signal.removeEventListener("abort", abort);
        if (!this.pending.size && this.timer) {
          clearTimeout(this.timer);
          this.timer = undefined;
        }
      };
      const abort = () => { cleanup(); reject(signal.reason); };
      const check = () => {
        try {
          if (signal.aborted) { abort(); return true; }
          const value = probe();
          if (value === undefined) return false;
          cleanup();
          resolve(value);
        } catch (error) {
          cleanup();
          reject(error);
        }
        return true;
      };
      if (this.immediateBudget > 0) {
        this.immediateBudget--;
        if (!this.resetScheduled) {
          this.resetScheduled = true;
          setImmediate(() => { this.immediateBudget = 32; this.resetScheduled = false; });
        }
        if (check()) return;
      }
      if (signal.aborted) { abort(); return; }
      signal.addEventListener("abort", abort, { once: true });
      this.pending.add(check);
      this.schedule();
    });
  }

  private schedule(): void {
    if (!this.timer && this.pending.size) this.timer = setTimeout(() => this.tick(), 10);
  }

  private tick(): void {
    this.timer = undefined;
    const deadline = performance.now() + 4;
    // Rotate pending work so the same slow requests cannot monopolize each tick.
    for (const check of [...this.pending].slice(0, 32)) {
      this.pending.delete(check);
      if (!check()) this.pending.add(check);
      if (performance.now() >= deadline) break;
    }
    this.schedule();
  }
}

export const scheduler = new Scheduler();
