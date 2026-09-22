/* Human evidence stays separate from device reports and simulated events. */
const labels={planned:'建立制作档案',printing:'开始制作',printed:'打印完成',failed:'制作失败',verified:'装配检查',annotation:'补充证据'};
const transitions={planned:['printing','printed'],printing:['printed','failed'],failed:['printing'],printed:['verified','failed'],verified:['failed'],bound:[]};
export class FabricationUI{
 constructor(context){Object.assign(this,context);}
 measurement(){return `<div class="measurement-row"><label>项目<input name="measurement-name" maxlength="60" placeholder="例如安装孔径" required></label><label>实测值<input name="measurement-value" type="number" min="-10000" max="10000" step="any" required></label><label>单位<select name="measurement-unit"><option>mm</option><option>°C</option><option>g</option><option>N</option></select></label><label>测量方法<input name="measurement-method" maxlength="200" placeholder="仪器及测量条件" required></label><button type="button" class="btn ghost small" data-action="fabrication-remove-measure">删除测量</button></div>`;}
 async records(id){
  const out=await this.api(`/api/instances/${id}/records`),i=out.instance,e=this.esc,editable=!i.simulated&&!i.legacy_unverified&&i.manufacturing_version_id;
  const version=this.state().designs.find(d=>d.id===i.design_id)?.versions.find(v=>v.id===i.manufacturing_version_id);
  const links=[out.source?`<button class="btn ghost small" data-action="fabrication-records" data-id="${out.source.id}">来源：${e(out.source.name)}</button>`:'',...out.remakes.map(r=>`<button class="btn ghost small" data-action="fabrication-records" data-id="${r.id}">重制：${e(r.name)}</button>`)].join('');
  const actions=editable?`${(transitions[i.stage]||[]).map(stage=>`<button class="btn small" data-action="fabrication-stage" data-id="${id}" data-stage="${stage}">${stage==='printing'&&i.stage==='failed'?'同版本重试':labels[stage]}</button>`).join('')}<button class="btn ghost small" data-action="fabrication-stage" data-id="${id}" data-stage="annotation">补充证据</button>${i.stage==='failed'?`<button class="btn ghost small" data-action="fabrication-remake" data-id="${id}">建立关联重制档案</button>`:''}`:'';
  const body=`<p class="modal-copy mb">${e(i.name)} · ${version?'制造 v'+version.revision:'旧版本待核实'} · ${i.simulated?'模拟档案':'人工制作档案'}。照片和测量只对本人可见，记录不会自动证明安全或生成设备实测结果。</p><div class="card-actions mb">${actions}</div><div class="card-actions mb">${links}</div>`+(out.records.length?`<div class="stack">${out.records.map(r=>`<section class="version-card"><div class="row between"><h3>${labels[r.stage]||e(r.stage)}</h3><span class="pill">第 ${r.attempt} 次尝试</span></div><p class="modal-copy mt">${e(r.note)}</p><p class="quiet mt">${e(r.details.material||'')} ${e(r.details.machine||'')} · ${new Date(r.created*1000).toLocaleString('zh-CN')}</p>${Array.isArray(r.details.measurements)&&r.details.measurements.length?`<dl class="measurement-list">${r.details.measurements.map(m=>`<div><dt>${e(m.name)}</dt><dd>${e(m.value)} ${e(m.unit)} · ${e(m.method)}</dd></div>`).join('')}</dl>`:''}<div class="evidence-photos">${r.photos.map(p=>`<figure><a href="${p.url}" target="_blank" rel="noopener"><img src="${p.url}" alt="${e(p.name)}" loading="lazy"></a><figcaption>${e(p.name)}</figcaption><details><summary>照片校验信息</summary><code>${p.sha256}</code></details></figure>`).join('')}</div></section>`).join('')}</div>`:'<div class="notice">尚无制作记录。模拟绑定不会生成打印和装配证据。</div>');
  this.showModal('制作记录','失败和旧尝试会保留；模型改版后请建立关联的新档案。',body);
 }
 async record(id,stage){
  const out=await this.api(`/api/instances/${id}/records`),i=out.instance;
  this.showModal(labels[stage]||'记录制作','仅填写实际观察和测量；没有实物时无需登记。',`<form id="fabrication-form" data-id="${id}" data-stage="${stage}" data-expected="${i.stage}" data-key="${this.newId()}">${stage==='printed'||stage==='printing'?`<div class="transform-grid"><div class="field"><label>实际材料<input name="material" maxlength="120" ${stage==='printed'?'required':''}></label></div><div class="field"><label>打印设备或服务<input name="machine" maxlength="120" ${stage==='printed'?'required':''}></label></div></div>`:''}${stage==='verified'?`<div class="stack mb">${[['interface_fit','已检查接口尺寸与实际配合'],['retention','已检查紧固与可靠保持'],['electrical_thermal','已按实际条件完成电气与温度检查']].map(([k,v])=>`<label class="check"><input type="checkbox" name="${k}" required>${v}</label>`).join('')}</div>`:''}<div class="field"><label>${stage==='failed'?'失败现象与原因':'制作与检查记录'}<textarea name="note" minlength="8" maxlength="2000" required></textarea></label></div><div class="field"><label>现场照片（可选，最多 3 张，每张 2 MiB）<input type="file" name="photos" accept="image/png,image/jpeg" multiple></label><p class="quiet">保存时清除照片元数据；已存证照片随原记录保留。</p></div><div id="measurement-rows" class="stack"></div><button class="btn ghost small" type="button" data-action="fabrication-add-measure">添加测量值</button><label class="check mt"><input name="acknowledge" type="checkbox" required>我确认这是实际发生的记录，不是模拟结果。</label><div class="modal-actions"><button class="btn" type="submit">保存实际制作记录</button></div></form>`);
 }
 remake(id){
  const i=this.state().instances.find(x=>x.id===id),d=this.state().designs.find(x=>x.id===i?.design_id),versions=(d?.versions||[]).filter(v=>v.status==='approved');
  if(!versions.length)throw Error('请先在制造版本中批准一个用于重制的版本');
  this.showModal('建立关联重制档案','旧失败档案保留，新档案使用独立身份和选定的固定版本。',`<form id="fabrication-remake-form" data-id="${id}" data-key="${this.newId()}"><div class="field"><label>重制名称<input name="name" maxlength="100" value="${this.esc(i.name+' / 重制')}" required></label></div><div class="field"><label>已批准制造版本<select name="manufacturing_version_id">${versions.map(v=>`<option value="${v.id}">制造 v${v.revision}${v.id===i.manufacturing_version_id?' · 与失败档案相同':''}</option>`).join('')}</select></label></div><div class="field"><label>重制原因<textarea name="note" minlength="8" maxlength="2000" required></textarea></label></div><div class="modal-actions"><button class="btn" type="submit">创建关联档案</button></div></form>`);
 }
 async action(button){
  const {action,id,stage}=button.dataset;
  if(action==='fabrication-records')return this.records(id);
  if(action==='fabrication-stage')return this.record(id,stage);
  if(action==='fabrication-remake')return this.remake(id);
  if(action==='fabrication-add-measure'){
   const list=document.querySelector('#measurement-rows');if(list.children.length>=20)throw Error('最多 20 个测量值');list.insertAdjacentHTML('beforeend',this.measurement());return;
  }
  if(action==='fabrication-remove-measure')button.closest('.measurement-row').remove();
 }
 async submit(form){
  const values=Object.fromEntries(new FormData(form));
  if(form.id==='fabrication-remake-form'){
   const out=await this.api('/api/instances','POST',{request_key:form.dataset.key,source_instance_id:form.dataset.id,name:values.name,manufacturing_version_id:values.manufacturing_version_id,note:values.note});
   await this.refresh(true);return this.records(out.id);
  }
  const files=Array.from(form.querySelector('[name=photos]').files);
  if(files.length>3||files.some(f=>f.size>2*1024*1024))throw Error('最多选择 3 张照片，每张不超过 2 MiB');
  const photos=[];for(const file of files)photos.push({name:file.name,data_base64:await this.fileBase64(file)});
  const measurements=Array.from(form.querySelectorAll('.measurement-row')).map(row=>Object.fromEntries(['name','value','unit','method'].map(k=>[k,k==='value'?Number(row.querySelector('[name=measurement-value]').value):row.querySelector(`[name=measurement-${k}]`).value])));
  await this.api(`/api/instances/${form.dataset.id}/records`,'POST',{stage:form.dataset.stage,expected_stage:form.dataset.expected,request_key:form.dataset.key,note:values.note,acknowledge:values.acknowledge==='on',photos,details:{material:values.material,machine:values.machine,measurements,interface_fit:values.interface_fit==='on',retention:values.retention==='on',electrical_thermal:values.electrical_thermal==='on'}});
  await this.refresh(true);return this.records(form.dataset.id);
 }
}
