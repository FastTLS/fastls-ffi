"""Portable, self-contained HTML. Results are only comparable within a case/host."""
import html
import json
from pathlib import Path
from statistics import median


def summaries(reports):
    groups={}
    for report in reports:
        for run in report["runs"]:
            key=(report["environment_id"],run["case"]["name"],run["client"])
            run=dict(run,_expected_rounds=report.get("rounds"))
            if report.get("source_changed_during_run"):
                run=dict(run,valid=False,error="benchmark source changed during this run")
            groups.setdefault(key,[]).append(run)
    result=[]
    for (environment,case,client),runs in groups.items():
        good=[r for r in runs if r.get("valid")]
        row=dict(environment=environment,case=case,client=client,rounds=len(runs),valid_rounds=len(good),
                 failed=sum(r.get("metrics",{}).get("failed",0) for r in runs),
                 errors=[r.get("error") for r in runs if r.get("error")])
        expected_rounds=runs[0].get("_expected_rounds") or len(runs)
        row["expected_rounds"]=expected_rounds
        if good and len(good)==len(runs)==expected_rounds:
            for name in ("rps","mib_per_second","p50_seconds","p95_seconds","ttfb_p50_seconds","elapsed_seconds"):
                values=[r["metrics"][name] for r in good if r["metrics"].get(name) is not None]
                if values: row[name]=dict(median=median(values),minimum=min(values),maximum=max(values))
            for name in ("cpu_cores","peak_rss_bytes","peak_threads","peak_descriptors"):
                values=[r["resources"][name] for r in good if r["resources"].get(name) is not None]
                if values: row[name]=dict(median=median(values),minimum=min(values),maximum=max(values))
        result.append(row)
    return result


def render(reports,path):
    # Cold-load runs may contain thousands of connection observations. Preserve
    # every one in results.json; the browser report embeds bounded summaries.
    compact=[]
    for report in reports:
        item={key:value for key,value in report.items() if key!="runs"}
        item["runs"]=[]
        for run in report["runs"]:
            row={key:value for key,value in run.items() if key not in ("metrics","resources","markers")}
            row["metrics"]={key:value for key,value in run.get("metrics",{}).items() if key!="server"}
            server=run.get("metrics",{}).get("server",{})
            row["server"]={key:value for key,value in server.items() if key!="connections"}
            row["server"]["connection_count"]=len(server.get("connections",{}))
            row["server"]["tls_resumed_connections"]=sum(bool(c["resumed"]) for c in server.get("connections",{}).values())
            row["resources"]={key:value for key,value in run.get("resources",{}).items() if key!="samples"}
            row["markers"]=[event for event in run.get("markers",[]) if event["stage"]!="progress"]
            item["runs"].append(row)
        compact.append(item)
    payload=json.dumps(dict(reports=compact,summary=summaries(reports)),ensure_ascii=False).replace("<","\\u003c")
    page='''<!doctype html><html lang="zh-CN"><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>HTTP 客户端实测报告</title>
<style>body{font:14px/1.65 system-ui,"Microsoft YaHei",sans-serif;color:#23272b;margin:24px}h1{font-size:24px}h2{font-size:19px;margin-top:28px}.muted{color:#626a72}.scroll{overflow:auto;border:1px solid #dde2e7}table{border-collapse:collapse;width:100%;white-space:nowrap}td,th{padding:9px 12px;text-align:left;border-bottom:1px solid #e5e8eb}th{background:#f4f6f8}select,button{padding:7px;margin:8px 8px 15px 0;font:inherit}tr:nth-child(even){background:#fafbfc}pre{white-space:pre-wrap;overflow-wrap:anywhere}details{margin:12px 0}.notice{padding:12px;background:#f4f6f8;max-width:1100px}</style>
<h1>curl_cffi、Requests 与 fastls-ffi 实测</h1><p class="notice">只比较相同环境、场景与协议。smoke/quick是诊断，不是正式性能结论；full每组至少五轮。量化分位数是2%桶上界。慢消费者吞吐受指定消费速率限制，SSE解析行统计事件数据字节，不能与原始传输吞吐混排。</p><p id="scope" class="muted"></p><select id="env" aria-label="环境"></select><select id="case" aria-label="场景"></select><button onclick="window.print()">打印 / 保存PDF</button><div class="scroll" id="table"></div>
<h2>运行条件与限制</h2><p>Requests使用线程独立Session，其它异步客户端使用各自事件循环。证书校验开启，禁止环境代理、重试和重定向；协议及连接由服务器记录，TLS配方不保证相同。CPU包含原生运行时；RSS是采样峰值，不是精确瞬时峰值，且包含客户端指标收集开销。服务器资源单独采样。空闲读取和冷启动另列；此页嵌入逐轮摘要，完整连接记录、进度和资源采样保留在配套results.json。</p><h2>逐轮摘要与环境</h2><div id="details"></div><script>const data=__DATA__;
const make=(tag,text)=>{const e=document.createElement(tag);if(text!==undefined)e.textContent=text;return e};const env=document.querySelector('#env'),which=document.querySelector('#case');
for(const [select,items] of [[env,['全部环境',...new Set(data.summary.map(r=>r.environment))]],[which,['全部场景',...new Set(data.summary.map(r=>r.case))]]])for(const name of items){const o=make('option',name);o.value=name;select.append(o)}
function value(row,key,scale=1){const v=row[key];if(!v)return '未取得';return (v.median*scale).toFixed(2)+' ['+(v.minimum*scale).toFixed(2)+', '+(v.maximum*scale).toFixed(2)+']'}
function show(){const t=make('table'),head=make('tr');['环境 / 场景','客户端','有效轮 / 总轮','失败请求','RPS','MiB/s','p50 ms','p95 ms','TTFB p50 ms','CPU核','RSS MiB','线程数','FD/句柄'].forEach(s=>head.append(make('th',s)));t.append(head);for(const r of data.summary){if(env.value!=='全部环境'&&r.environment!==env.value||which.value!=='全部场景'&&r.case!==which.value)continue;const tr=make('tr');[r.environment+' / '+r.case,r.client,r.valid_rounds+'/'+r.rounds,r.failed,value(r,'rps'),value(r,'mib_per_second'),value(r,'p50_seconds',1000),value(r,'p95_seconds',1000),value(r,'ttfb_p50_seconds',1000),value(r,'cpu_cores'),value(r,'peak_rss_bytes',1/1048576),value(r,'peak_threads'),value(r,'peak_descriptors')].forEach(v=>tr.append(make('td',String(v))));t.append(tr)}document.querySelector('#table').replaceChildren(t)}env.onchange=which.onchange=show;show();
document.querySelector('#scope').textContent=data.reports.map(r=>r.environment_id+' · '+r.profile+' · '+r.runs.length+'次独立子进程运行').join('；')+'。单元格为跨轮中位数 [最小, 最大]，错误轮不参加性能汇总但保留计数。';for(const r of data.reports){const d=make('details'),s=make('summary',r.environment_id+' · '+r.profile+' · 原始结果');d.append(s,make('pre',JSON.stringify(r,null,2)));document.querySelector('#details').append(d)}
</script></html>'''
    Path(path).write_text(page.replace("__DATA__",payload),encoding="utf-8")


if __name__=="__main__":
    import argparse
    p=argparse.ArgumentParser();p.add_argument("inputs",nargs="+");p.add_argument("--output",required=True);args=p.parse_args()
    render([json.loads(Path(path).read_text()) for path in args.inputs],args.output)
