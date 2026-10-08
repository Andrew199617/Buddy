import { readFileSync, writeFileSync } from 'node:fs';
import { resolve } from 'node:path';

const arguments_ = process.argv.slice(2);
const outputIndex = arguments_.indexOf('--output');
if (outputIndex < 0 || !arguments_[outputIndex + 1]) {
	throw new Error('Provide report JSON paths followed by --output report.html');
}
function prepareBrowserReport(report) {
	const runs = report.runs.map((run) => {
		const browserRun = { ...run };
		delete browserRun.cpuProfile;
		return browserRun;
	});
	return { ...report, runs };
}

const reports = arguments_
	.slice(0, outputIndex)
	.map((path) => JSON.parse(readFileSync(path, 'utf8')))
	.map(prepareBrowserReport);
const outputPath = resolve(arguments_[outputIndex + 1]);
const data = JSON.stringify(reports).replace(/</g, '\\u003c');
const html = `<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>Buddy startup measurements</title>
<style>
body{font:14px system-ui,sans-serif;background:#faf8f5;color:#202b23;margin:0;padding:28px}main{max-width:1440px;margin:auto}h1{font-size:27px;margin:0 0 6px}p{color:#57665b;line-height:1.55}table{border-collapse:collapse;width:100%;background:white}th,td{text-align:left;padding:10px;border-bottom:1px solid #e6ebe7;font-variant-numeric:tabular-nums}th{background:#eaf1ea;font-size:12px}section{margin-top:28px}select,input{font:inherit;padding:8px;border:1px solid #bbc9be;border-radius:6px;background:white;margin-right:10px}label{display:inline-block;margin:5px 0}.legend{display:flex;gap:18px;color:#536358;margin:14px 0}.dot{display:inline-block;width:11px;height:11px;margin-right:5px}.script{background:#567a61}.api{background:#bd8644}.other{background:#88a1ad}.longtask{background:#b9594d}.waterfall{background:white;border:1px solid #dfe6df;overflow:auto;max-height:640px}.row{display:grid;grid-template-columns:minmax(250px,39%) minmax(380px,1fr);height:25px;border-bottom:1px solid #f0f2ef}.name{padding:4px 8px;white-space:nowrap;overflow:hidden;text-overflow:ellipsis;font:11px ui-monospace,monospace}.track{position:relative;background:repeating-linear-gradient(to right,transparent 0,transparent calc(10% - 1px),#eef1ed calc(10% - 1px),#eef1ed 10%)}.bar{position:absolute;top:7px;height:11px;border-radius:2px;min-width:2px}.marker{position:absolute;top:0;bottom:0;border-left:1px dashed #222;pointer-events:none}.caption{font-size:12px;color:#667568}.stats{display:flex;flex-wrap:wrap;gap:12px}.stat{padding:12px 18px;background:white;border:1px solid #e2e8e1;border-radius:8px}.stat strong{display:block;font-size:22px}.warning{background:#fff3df;padding:12px;border-radius:6px}@media(max-width:700px){body{padding:15px}.table-wrap{overflow:auto}.row{grid-template-columns:230px 500px}}
</style>
</head>
<body><main><h1>Buddy startup measurements</h1><p>Navigation to a verified first draft keystroke. All API traffic is real; no chat or provider action is submitted. Requests, queries and account identifiers are sanitized. Phone results use Chromium emulation and controlled throttling, rather than a physical iPhone.</p>
<div class="warning">Five samples per group: p90 is the observed maximum using nearest rank. Cold means a fresh private context with an empty HTTP cache; warm means a new page in the same context. Backend caches are retained. CPU-profile diagnostic runs are excluded from the comparison table. Blocking is the custom navigation-to-verified-draft sum of max(long-task duration − 50 ms, 0); it is not Lighthouse TBT or INP.</div>
<section><h2>Controlled targets</h2><p>Desktop: 1440 × 1000, CPU 1×, 20 Mbps down / 5 Mbps up, 40 ms latency. Phone: 428 × 926, CPU 4×, 4 Mbps down / 1 Mbps up, 150 ms latency. Production stages use the same HTTP tailnet origin. Script counts are fetched resources, rather than modules inside a bundle.</p><div class="table-wrap"><table><thead><tr><th>Stage</th><th>URL</th><th>Source provenance</th></tr></thead><tbody id="targets"></tbody></table></div></section>
<section><h2>Repeated results</h2><div class="table-wrap"><table><thead><tr><th>Target / cache</th><th>Samples</th><th>Splash median / p90</th><th>Editable median / p90</th><th>Model median / p90</th><th>Verified draft median / p90</th><th>Transfer median</th><th>Scripts</th><th>Blocking time</th></tr></thead><tbody id="summary"></tbody></table></div></section>
<section><h2>Request waterfall</h2><label>Run <select id="run"></select></label><label>Resources <select id="type"><option value="all">All</option><option value="Script">Scripts</option><option value="api">API</option><option value="longtask">Long tasks</option></select></label><label>Path filter <input id="filter" type="search" placeholder="e.g. settings or lowlight"></label>
<div class="legend"><span><i class="dot script"></i>Script</span><span><i class="dot api"></i>API</span><span><i class="dot other"></i>Other</span><span><i class="dot longtask"></i>Long task</span><span>Dashed markers: splash and verified draft</span></div><div id="stats" class="stats"></div><p id="caption" class="caption"></p><div id="waterfall" class="waterfall"></div></section>
<section><h2>API durations</h2><div class="table-wrap"><table><thead><tr><th>Method / path</th><th>Start</th><th>Duration</th><th>Status</th></tr></thead><tbody id="apis"></tbody></table></div></section>
</main>
<script>
const reports=${data};
const flattened=[];
const escape=(value)=>String(value).replace(/[&<>"']/g,(character)=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[character]));
const seconds=(value)=>value===null||value===undefined?'—':(value/1000).toFixed(2)+' s';
const bytes=(value)=>(value/1000000).toFixed(2)+' MB';
const primary=reports.filter((report)=>!report.diagnostic);
document.getElementById('targets').innerHTML=primary.map((report)=>'<tr><td>'+escape(report.label)+'</td><td>'+escape(report.targetUrl)+'</td><td>'+escape(report.revision)+'</td></tr>').join('');
document.getElementById('summary').innerHTML=primary.flatMap((report)=>Object.entries(report.summary).map(([group,data])=>{
 const m=data.metrics;
 return '<tr><td>'+escape(report.label+' / '+group)+'</td><td>'+data.passedCount+'/'+data.sampleCount+'</td><td>'+seconds(m.splashRemovedMs.medianMs)+' / '+seconds(m.splashRemovedMs.p90Ms)+'</td><td>'+seconds(m.composerEditableMs.medianMs)+' / '+seconds(m.composerEditableMs.p90Ms)+'</td><td>'+seconds(m.selectedModelReadyMs.medianMs)+' / '+seconds(m.selectedModelReadyMs.p90Ms)+'</td><td>'+seconds(m.firstDraftVerifiedMs.medianMs)+' / '+seconds(m.firstDraftVerifiedMs.p90Ms)+'</td><td>'+bytes(data.medianTransferBytes)+'</td><td>'+data.medianScriptCount+'</td><td>'+seconds(data.medianLongTaskBlockingMs)+'</td></tr>';
})).join('');
for(const report of reports){for(const run of report.runs){flattened.push({...run,label:report.label,diagnostic:Boolean(report.diagnostic)});}}
const runSelect=document.getElementById('run');
runSelect.innerHTML=flattened.map((run,index)=>'<option value="'+index+'">'+escape(run.label+' / '+run.profile+' / '+run.cache+' / pair '+run.pairIndex+(run.diagnostic?' / diagnostic':''))+'</option>').join('');
function draw(){
 const run=flattened[Number(runSelect.value)];
 const type=document.getElementById('type').value;
 const search=document.getElementById('filter').value.toLowerCase();
 const milestones=run.probe?.milestones||{};
 const end=Math.max(milestones.firstDraftVerifiedMs||0,...run.resources.map((resource)=>resource.endMs||0),1)*1.025;
 const rows=run.resources.map((resource)=>({...resource,label:new URL(resource.url).pathname,kind:new URL(resource.url).pathname.startsWith('/api/')?'api':resource.type==='Script'?'script':'other'}));
 const longTasks=(run.probe?.longTasks||[]).map((task,index)=>({label:'Long task '+(index+1),kind:'longtask',type:'longtask',startMs:task.startMs,durationMs:task.durationMs,endMs:task.startMs+task.durationMs,encodedBytes:0}));
 rows.push(...longTasks);
 const filtered=rows.filter((resource)=>(type==='all'||type==='api'&&resource.kind==='api'||type==='longtask'&&resource.kind==='longtask'||type==='Script'&&resource.type==='Script')&&resource.label.toLowerCase().includes(search)).sort((left,right)=>left.startMs-right.startMs);
 document.getElementById('stats').innerHTML='<div class="stat"><strong>'+seconds(milestones.firstDraftVerifiedMs)+'</strong>Verified draft</div><div class="stat"><strong>'+bytes(run.summary.encodedTransferBytes)+'</strong>Transferred</div><div class="stat"><strong>'+run.summary.uniqueScriptCount+'</strong>Script resources</div><div class="stat"><strong>'+seconds(run.summary.longTaskBlockingMs)+'</strong>Long-task blocking</div><div class="stat"><strong>'+run.summary.cachedRequestCount+'</strong>Cached requests</div>';
 document.getElementById('caption').textContent=filtered.length+' rows · 0–'+seconds(end)+' · '+run.pageErrors.length+' page errors · '+run.summary.mutatingRequestCount+' automatic startup POSTs';
 document.getElementById('waterfall').innerHTML=filtered.map((resource)=>{
  const left=resource.startMs/end*100;
  const width=Math.max(0.12,(resource.durationMs||0)/end*100);
  const title=resource.label+' | '+seconds(resource.startMs)+' start | '+seconds(resource.durationMs)+' duration | '+bytes(resource.encodedBytes)+' | status '+(resource.wireStatus||resource.status||'');
  return '<div class="row"><div class="name" title="'+escape(title)+'">'+escape(resource.label)+'</div><div class="track"><span class="bar '+resource.kind+'" style="left:'+left+'%;width:'+width+'%" title="'+escape(title)+'"></span><span class="marker" style="left:'+((milestones.splashRemovedMs||0)/end*100)+'%"></span><span class="marker" style="left:'+((milestones.firstDraftVerifiedMs||0)/end*100)+'%"></span></div></div>';
 }).join('');
 document.getElementById('apis').innerHTML=run.summary.apiDurations.map((api)=>'<tr><td>'+escape(api.method+' '+api.path)+'</td><td>'+seconds(api.startMs)+'</td><td>'+seconds(api.durationMs)+'</td><td>'+api.status+'</td></tr>').join('');
}
runSelect.addEventListener('change',draw);
document.getElementById('type').addEventListener('change',draw);
document.getElementById('filter').addEventListener('input',draw);
draw();
</script>
</body></html>`;
writeFileSync(outputPath, html);
console.log('Wrote sanitized interactive startup report: ' + outputPath);
