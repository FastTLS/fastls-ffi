export function responseEncoding(contentType: string | undefined): string {
  return /(?:^|;)\s*charset\s*=\s*(?:"([^"]+)"|'([^']+)'|([^;\s]+))/i.exec(contentType ?? "")?.slice(1).find(Boolean)?.toLowerCase() ?? "utf-8";
}
export function decodeText(bytes: Uint8Array, encoding: string): string {
  // WHATWG aliases ISO-8859-1 to Windows-1252. Preserve the declared Latin-1
  // byte mapping, matching Python for the full 0..255 range.
  if (/^(?:iso-8859-1|latin-?1)$/i.test(encoding)) return Buffer.from(bytes.buffer,bytes.byteOffset,bytes.byteLength).toString("latin1");
  return new TextDecoder(encoding, { ignoreBOM: true }).decode(bytes);
}
export function decodeJSON(bytes: Buffer): string {
  let encoding = "utf-8", start=0;
  if (bytes.length >= 4 && ((bytes[0]===0 && bytes[1]===0 && bytes[2]===254 && bytes[3]===255) || (bytes[0]===255 && bytes[1]===254 && bytes[2]===0 && bytes[3]===0))) { encoding=bytes[0]===0?"utf-32be":"utf-32le";start=4; }
  else if (bytes[0]===254 && bytes[1]===255) { encoding="utf-16be";start=2; }
  else if (bytes[0]===255 && bytes[1]===254) { encoding="utf-16le";start=2; }
  else if (bytes.length>=4) {
    if (bytes[0]===0) encoding=bytes[1]===0?"utf-32be":"utf-16be";
    else if (bytes[1]===0) encoding=bytes[2]===0 && bytes[3]===0?"utf-32le":"utf-16le";
  }
  else if (bytes.length===2) { if (bytes[0]===0) encoding="utf-16be"; else if (bytes[1]===0) encoding="utf-16le"; }
  if (encoding.startsWith("utf-32")) {
    if ((bytes.length-start)%4) throw new TypeError("invalid UTF-32 JSON");
    const out: string[]=[];
    for(let offset=start;offset<bytes.length;offset+=4){const cp=encoding==="utf-32le"?bytes.readUInt32LE(offset):bytes.readUInt32BE(offset);if(cp>0x10ffff || (cp>=0xd800 && cp<=0xdfff))throw new TypeError("invalid UTF-32 JSON");out.push(String.fromCodePoint(cp));}
    return out.join("");
  }
  return new TextDecoder(encoding,{fatal:true}).decode(bytes.subarray(start));
}
