import {datasets,spotchecks} from '../api/entities.js';
import {workspace} from '../stores/workspace.js';
import {session} from '../stores/session.js';
import {heading} from '../components/common/shell.js';
import {table} from '../components/common/table.js';
import {esc,fmt,date,notify} from '../utils/dom.js';
import {submit} from '../hooks/forms.js';
import {SpotStatus} from '../constants/enums.js';
const canWrite=()=>session.user.role!=='viewer';
const requestNo=()=>'claim-'+Date.now().toString(36)+'-'+Math.random().toString(36).slice(2,8);
const reportNo=()=>'report-'+Date.now().toString(36)+'-'+Math.random().toString(36).slice(2,8);
let claimRequest=requestNo();

function rosterTable(check){
 const rows=check.items.map(item=>({
  position:item.position+1,
  asset:item.asset,
  model:item.model,
  original_km:item.original_km,
  reported_km:item.reported_km,
  failed:item.reported_failed===null?'':(item.reported_failed?'失效':'在役'),
  report_no:item.report_no||'',
  state:item.reported?'已回报':'缺报',
 }));
 return table([
  {key:'position',label:'#'},{key:'asset',label:'资产号'},{key:'model',label:'型号'},
  {key:'original_km',label:'冻结原里程'},{key:'reported_km',label:'回报里程'},
  {key:'failed',label:'回报状态'},{key:'report_no',label:'回报号'},{key:'state',label:'回收'}
 ],rows,'名单尚未生成。');
}

function missingList(check){
 if(check.status!=='open')return '';
 if(!check.missing.length)return '<p class="warning">全部 '+check.size+' 件已回报，可以结案生成新批次。</p>';
 return '<div class="missing-box"><strong>缺报 '+check.missing.length+' 件</strong><p>'+check.missing.map(item=>esc(item.asset)).join('、')+'</p><p class="hint">缺报件不会被估算成 0 公里，也不会进入新批次样本。</p></div>';
}

function reportForm(check){
 if(!canWrite()||check.status!=='open'||!check.missing.length)return '';
 return '<form id="spot-report-form"><div class="parameter-grid"><label>缺报资产<select name="asset">'+check.missing.map(item=>'<option value="'+esc(item.asset)+'">'+esc(item.asset)+' · 原里程 '+fmt(item.original_km)+'</option>').join('')+'</select></label><label>本次回报里程<input name="km" type="number" step="any" min="1" required placeholder="不低于冻结原里程"></label><label>失效状态<select name="failed"><option value="1">已失效</option><option value="0">截止仍在役</option></select></label><label>回报号<input name="report_no" required value="'+esc(reportNo())+'"></label></div><button type="submit" class="primary">提交单件回报</button><p class="hint">回报号用于重试去重；同一回报号换内容会被拒绝。里程低于冻结原里程会被拒收。</p></form>';
}

function actionForms(check){
 if(!canWrite()||check.status!=='open')return '';
 return '<div class="spot-actions"><form id="spot-close-form"><label>结案号<input name="close_no" value="close-'+check.id+'" required></label><button type="submit" class="primary" '+(check.complete?'':'disabled')+'>'+(check.complete?'结案并生成新批次':'仍有缺报，不能结案')+'</button></form><button class="button" id="spot-cancel">撤销抽检并释放占用</button></div>'+(!check.complete?'<p class="hint">结案整单处理：只有全部回报齐全才会建立新批次，原批次保持不变。</p>':'');
}

function detailPanel(check){
 const generated=check.generated_dataset;
 return '<section class="panel"><div class="section-title"><h2>'+esc(check.name)+'</h2><span>'+esc(SpotStatus[check.status]||check.status)+' · 回报 '+check.reported_count+' / '+check.size+'</span></div><div class="spot-meta"><span>来源批次<strong>'+esc(check.dataset_name||'—')+'</strong></span><span>型号<strong>'+esc(check.model||'全部')+'</strong></span><span>领取时间<strong>'+date(check.created_at)+'</strong></span><span>名单指纹<strong>'+esc(check.roster_checksum.slice(0,16))+'…</strong></span>'+(check.closed_at?'<span>结案时间<strong>'+date(check.closed_at)+'</strong></span>':'')+'</div>'+missingList(check)+(check.status==='open'?reportForm(check):'')+'</section>'+(generated?'<section class="panel"><div class="section-title"><h2>已生成新批次</h2><span>原批次保留，不改写</span></div><p>'+esc(generated.name)+' · '+generated.rows+' 条样本 · SHA256 '+esc(generated.checksum.slice(0,18))+'…</p><p class="hint">可在数据接入页查看该批次，或在工作台执行删失寿命拟合。</p></section>':'')+actionForms(check)+'<section class="panel"><div class="section-title"><h2>冻结名单与逐件回报</h2><span>'+check.size+' 件</span></div>'+rosterTable(check)+'</section>';
}

export async function spotPage(id){
 const [list,allDatasets]=await Promise.all([spotchecks.list(),datasets.list()]);workspace.spotchecks=list;workspace.datasets=allDatasets;
 const selected=id?(await spotchecks.get(id)):(list[0]||null);
 workspace.selectedCheck=selected?.id||null;
 const open=list.filter(c=>c.status==='open');
 const modelParam=(workspace.meta.parameters||[]).find(p=>p.key==='model');
 const modelOptions=(modelParam?.options||[]).map(o=>'<option value="'+esc(o.value)+'">'+esc(o.label)+'</option>').join('');
 return heading('06 / FIELD SAMPLING','在役件抽检回报','从指定批次的在役件随机抽取并冻结名单与原里程；同一资产跨批次只参加一项未结抽检。逐件回报里程与失效状态，齐全后结案生成新批次。')+'<div class="spot-grid"><section class="panel"><div class="section-title"><h2>领取抽检单</h2><span>数量不足整单拒绝</span></div><form id="spot-claim-form"><label>抽检单名称<input name="name" value="在役件月度抽检" minlength="2" required></label><label>来源数据批次<select name="dataset_id" required>'+allDatasets.map(d=>'<option value="'+d.id+'">'+esc(d.name)+' · '+d.rows+' 条</option>').join('')+'</select></label><div class="parameter-grid"><label>抽取数量<input name="size" type="number" min="1" max="1000" value="10" required></label><label>限定型号<select name="model"><option value="">全部型号</option>'+modelOptions+'</select></label></div><button type="submit" class="primary" '+(canWrite()&&allDatasets.length?'':'disabled')+'>随机抽取并冻结名单</button><p class="hint">只从 failed=0 的在役件抽取；已被其他未结抽检占用的资产会跳过。请求号 '+esc(claimRequest)+' 重试不会重复抽取。</p></form><div class="section-title"><h2>抽检单</h2><span>'+open.length+' 项未结 / '+list.length+' 项</span></div><div class="batch-list">'+list.map(c=>'<button class="batch-item" data-spot="'+c.id+'"><strong>'+esc(c.name)+'</strong><span>'+esc(SpotStatus[c.status]||c.status)+' · 回报 '+c.reported_count+' / '+c.size+' · 缺报 '+c.missing_count+'</span><small>'+esc(c.dataset_name||'')+' · '+date(c.created_at)+'</small></button>').join('')+'</div></section><section>'+(selected?detailPanel(selected):'<div class="empty">尚未领取抽检单。选择批次与数量后随机抽取在役件。</div>')+'</section></div>';
}

export function bindSpot(refresh){
 document.querySelectorAll('[data-spot]').forEach(b=>b.onclick=()=>{location.hash='#/spotchecks/'+b.dataset.spot;});
 const claim=document.querySelector('#spot-claim-form');
 if(claim)submit(claim,async f=>{
  const check=await spotchecks.claim({name:f.get('name'),dataset_id:Number(f.get('dataset_id')),size:Number(f.get('size')),model:f.get('model')||null,request_no:claimRequest});
  claimRequest=requestNo();notify(check.replayed?'已恢复原抽检单，未重复抽取':'抽检单已领取，名单与原里程已冻结');location.hash='#/spotchecks/'+check.id;
  await refresh();
 });
 const report=document.querySelector('#spot-report-form');
 if(report)submit(report,async f=>{
  const id=workspace.selectedCheck;
  try{
   const check=await spotchecks.report(id,{asset:f.get('asset'),km:Number(f.get('km')),failed:Number(f.get('failed')),report_no:f.get('report_no')});
   notify(check.complete?'全部回报齐全，可结案生成新批次':'已收录回报，剩余 '+check.missing_count+' 件缺报');await refresh();
  }catch(error){notify(error.message,true);}
 });
 const close=document.querySelector('#spot-close-form');
 if(close)submit(close,async f=>{
  const id=workspace.selectedCheck;
  try{const check=await spotchecks.close(id,{close_no:f.get('close_no')});notify(check.replayed?'抽检单已结案，未重复建批':'已结案，生成新批次 '+check.generated_dataset.name);await refresh();}catch(error){notify(error.message,true);}
 });
 const cancel=document.querySelector('#spot-cancel');
 if(cancel)cancel.onclick=async()=>{
  if(!confirm('撤销后释放名单占用，并拒收此单的迟到回报。确定撤销？'))return;
  const id=workspace.selectedCheck;
  try{await spotchecks.cancel(id);notify('抽检单已撤销，名单占用已释放');await refresh();}catch(error){notify(error.message,true);}
 };
}
