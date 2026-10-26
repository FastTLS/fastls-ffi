import { setImmediate as turn } from "node:timers/promises";
import { AbortError, FastlsError, responseError } from "./errors.js";
import { Headers } from "./headers.js";
import type { Native, NativeInfo } from "./native.js";
import type { Notifier } from "./notifications.js";
import type { QueryParams } from "./request-options.js";
import { scheduler } from "./scheduler.js";

export interface WebSocketOptions {
  headers?: Record<string,string>; profile?: string; params?: QueryParams;
  proxy?: string | null; proxyCaFile?: string | null; subprotocols?: readonly string[];
  handshakeTimeoutMs?: number; readTimeoutMs?: number | null; writeTimeoutMs?: number;
  cookieMode?: "session" | "manual";
  closeTimeoutMs?: number; maxMessageBytes?: number; signal?: AbortSignal;
}
export class WebSocketClosed extends FastlsError {
  override name = "WebSocketClosed";
  constructor(readonly code = 1006, readonly reason = "") { super(`WebSocket closed (${code})`); }
}
export function wsBudget(value: number, name: string, maximum = 86400000): number {
  if (!Number.isSafeInteger(value) || value < 1 || value > maximum) throw new RangeError(`${name} must be 1..${maximum} milliseconds`);
  return value;
}
function closePayload(code: number, reason: string): Buffer {
  if (!Number.isInteger(code) || !([1000,1001,1002,1003,1007,1008,1009,1010,1011,1012,1013,1014].includes(code) || (code >= 3000 && code <= 4999))) throw new RangeError("invalid WebSocket close code");
  if (typeof reason !== "string" || /[\uD800-\uDFFF]/u.test(reason)) throw new TypeError("close reason must be well-formed Unicode");
  const text=Buffer.from(reason); if (text.length>123) throw new RangeError("close reason exceeds 123 UTF-8 bytes");
  const out=Buffer.allocUnsafe(text.length+2);out.writeUInt16BE(code);text.copy(out,2);return out;
}
const scratch=Buffer.allocUnsafe(65536);
export class WebSocket {
  private readonly controller = new AbortController();
  private closed=false; private reading=false; private writing=false;
  private failure?: Error; private last?: NativeInfo; private closing?: Promise<void>;
  private readonly externalAbort=()=>this.abort(new AbortError(undefined,{cause:this.external?.reason}));
  headers=new Headers(); subprotocol="";
  constructor(private readonly native:Native, private readonly handle:bigint, private readonly release:()=>void,
    readonly maxMessageBytes:number, private readonly closeTimeoutMs:number, private readonly notifier?:Notifier, private readonly external?:AbortSignal) {
    external?.addEventListener("abort",this.externalAbort,{once:true});
    if(external?.aborted)this.externalAbort();
  }
  private ensure():void { if(this.closed)throw this.failure??new FastlsError("WebSocket is closed"); }
  get info(): NativeInfo { if(this.closed && this.last)return this.last; this.ensure(); return this.last=this.native.call<NativeInfo>("info",this.handle); }
  get closeCode():number{return (this.closed?this.last:this.info)?.websocket?.close_code??1006;}
  get closeReason():string{return (this.closed?this.last:this.info)?.websocket?.close_reason??"";}
  private check(info:NativeInfo):void {
    if(info.state==="error")throw responseError(info);
    if(info.state==="eof")throw new WebSocketClosed(info.websocket?.close_code,info.websocket?.close_reason);
  }
  private wait<T>(probe:()=>T|undefined, signal=this.controller.signal):Promise<T> {
    return this.notifier?this.notifier.probe(this.handle,probe,signal):scheduler.wait(probe,signal);
  }
  /** @internal */
  async opened():Promise<this> {
    try {
      const info=await this.wait(()=>{const i=this.info;if(i.status===101 && (i.state==="headers"||i.state==="eof"))return i;this.check(i);return i.state==="pending"?undefined:i;});
      this.headers=new Headers(info.header_values,info.headers);this.subprotocol=info.websocket?.subprotocol??"";return this;
    }catch(error){this.abort(error instanceof Error?error:undefined);throw error;}
  }
  private async write(opcode:number,data:Uint8Array,signal=this.controller.signal):Promise<void> {
    this.ensure();if(this.writing)throw new FastlsError("another WebSocket send is active");this.writing=true;
    try {
      let accepted=false;
      await this.wait(()=>{
        const info=this.info;this.check(info);
        if(accepted)return !info.websocket!.write_pending?true:undefined;
        const code=this.native.wsSend(this.handle,opcode,data);
        if(code<0){this.check(this.info);throw new FastlsError("invalid WebSocket send or connection is closing");}
        if(code!==0 && code!==1)throw new FastlsError("invalid WebSocket send status");
        if(code===0){accepted=true;const next=this.info;this.check(next);if(!next.websocket!.write_pending)return true;}
        return undefined;
      },signal);
    }catch(error){this.abort(error instanceof Error?error:undefined);throw error;}finally{this.writing=false;}
  }
  send(message:string|Uint8Array):Promise<void> {
    if(typeof message!=="string" && !(message instanceof Uint8Array))return Promise.reject(new TypeError("WebSocket messages must be strings or bytes"));
    if(typeof message==="string" && /[\uD800-\uDFFF]/u.test(message))return Promise.reject(new TypeError("WebSocket text must be well-formed Unicode"));
    if((typeof message==="string"?Buffer.byteLength(message):message.byteLength)>this.maxMessageBytes)return Promise.reject(new RangeError("WebSocket message exceeds maxMessageBytes"));
    // Copy before any await: accepted data must not observe caller mutation.
    return this.write(typeof message==="string"?1:2,Buffer.from(message));
  }
  private control(opcode:number,data:Uint8Array):Promise<void> {
    if(!(data instanceof Uint8Array))return Promise.reject(new TypeError("control payload must be bytes"));
    if(data.byteLength>125)return Promise.reject(new RangeError("control payload exceeds 125 bytes"));
    return this.write(opcode,Buffer.from(data));
  }
  ping(data:Uint8Array=Buffer.alloc(0)):Promise<void>{return this.control(9,data);}
  pong(data:Uint8Array=Buffer.alloc(0)):Promise<void>{return this.control(10,data);}
  async recv():Promise<string|Buffer> {
    this.ensure();if(this.reading)throw new FastlsError("another WebSocket recv is active");this.reading=true;
    try {
      let content=Buffer.alloc(0),length=0,kind:number|undefined,turns=0;
      while(true){
        const block=await this.wait(()=>{
          this.ensure();const r=this.native.wsRead(this.handle,scratch);
          if(r.code===1)return undefined;
          if(r.code!==0){this.check(this.info);throw new WebSocketClosed();}
          if(![1,2].includes(r.opcode))throw new FastlsError("invalid WebSocket message opcode");
          return {...r,data:Buffer.from(scratch.subarray(0,r.length))};
        });
        kind??=block.opcode;if(block.opcode!==kind)throw new FastlsError("WebSocket message opcode changed mid-message");
        const size=length+block.length;if(size>this.maxMessageBytes)throw new FastlsError("WebSocket message exceeds maxMessageBytes");
        if(size>content.length){const grown=Buffer.allocUnsafe(Math.min(this.maxMessageBytes,Math.max(size,content.length*2)));content.copy(grown,0,0,length);content=grown;}
        block.data.copy(content,length);length=size;
        if(block.end){const bytes=content.subarray(0,length);return kind===1?new TextDecoder("utf-8",{fatal:true,ignoreBOM:true}).decode(bytes):Buffer.from(bytes);}
        if(++turns%16===0)await turn();
      }
    }catch(error){this.abort(error instanceof Error?error:undefined);throw error;}finally{this.reading=false;}
  }
  closeAsync(code=1000,reason=""):Promise<void> {
    const data=closePayload(code,reason);if(this.closing)return this.closing;if(this.closed)return Promise.resolve();
    this.closing=(async()=>{
      const budget=new AbortController(),timer=setTimeout(()=>budget.abort(new FastlsError("WebSocket close timed out")),this.closeTimeoutMs);
      const signal=AbortSignal.any([this.controller.signal,budget.signal]);
      try{if(this.info.state==="headers"){
        // A close may interrupt a pending reader/writer after its total budget.
        if(!this.writing)await this.write(8,data,signal);
        await this.wait(()=>{const info=this.info;return info.state!=="headers"?true:undefined;},signal);
      }}catch{/* Closing always releases the handle, including a stalled peer. */}
      finally{clearTimeout(timer);this.abort();}
    })();return this.closing;
  }
  abort(error?:Error):void {
    if(this.closed)return;
    try{this.last=this.native.call<NativeInfo>("info",this.handle);}catch{/* Retain last metadata on teardown. */}
    this.closed=true;this.failure=error;this.external?.removeEventListener("abort",this.externalAbort);
    this.controller.abort(error??new FastlsError("WebSocket is closed"));
    try{this.native.call("close",this.handle);}finally{this.release();}
  }
}
