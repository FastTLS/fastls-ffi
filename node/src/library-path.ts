import { createHash } from "node:crypto";
import { existsSync, readFileSync, realpathSync, statSync } from "node:fs";
import { resolve } from "node:path";
import { FastlsError } from "./errors.js";

const verified = new Set<string>();
let glibcRuntime: string | undefined;
let glibcChecked = false;

/** Explicit overrides take priority; a present candidate manifest must validate. */
export function libraryPath(library?: string, packageRoot = resolve(__dirname, "..")): string {
  const override = library ?? process.env.FASTLS_LIBRARY;
  const name = process.platform === "win32" ? "fastls.dll"
    : process.platform === "darwin" ? "libfastls.dylib" : "libfastls.so";
  try {
    if (override !== undefined) return realpathSync(resolve(override));
    const manifestPath = resolve(packageRoot, "native/manifest.json");
    if (!existsSync(manifestPath)) {
      if (!existsSync(resolve(packageRoot, "src/native.ts"))) throw new Error("installed package has no native manifest; supply an external library");
      return realpathSync(resolve(packageRoot, "../dist", name));
    }
    const manifest = JSON.parse(readFileSync(manifestPath, "utf8"));
    if (manifest.schema !== 1 || manifest.abi !== 1 || manifest.platform !== process.platform || manifest.arch !== process.arch) {
      throw new Error(`candidate platform mismatch: expected ${process.platform}/${process.arch}, received ${manifest.platform}/${manifest.arch}`);
    }
    if (manifest.file !== name || !/^[a-f0-9]{64}$/.test(manifest.sha256)) throw new Error("invalid candidate library manifest");
    if (process.platform === "linux") {
      if (!glibcChecked) {
        const report = process.report.getReport() as { header?: { glibcVersionRuntime?: string } };
        glibcRuntime = report.header?.glibcVersionRuntime;
        glibcChecked = true;
      }
      const current = glibcRuntime;
      const minimum = manifest.requirements?.glibc;
      if (!current || !/^\d+\.\d+$/.test(minimum)) throw new Error("this candidate requires Linux glibc; musl is not supported");
      const [major, minor] = current.split(".").map(Number), [minMajor, minMinor] = minimum.split(".").map(Number);
      if (major < minMajor || (major === minMajor && minor < minMinor)) throw new Error(`candidate requires glibc >= ${minimum}; found ${current}`);
    }
    const path = realpathSync(resolve(packageRoot, "native", name)), stat = statSync(path);
    const key = `${path}:${stat.size}:${stat.mtimeMs}:${stat.ctimeMs}:${manifest.sha256}`;
    if (!verified.has(key)) {
      if (createHash("sha256").update(readFileSync(path)).digest("hex") !== manifest.sha256) throw new Error("candidate library SHA-256 mismatch");
      verified.add(key);
    }
    return path;
  } catch (cause) {
    throw new FastlsError("Cannot find Fastls library or validate native candidate; set FASTLS_LIBRARY or pass { library: absolutePath }", { cause });
  }
}
