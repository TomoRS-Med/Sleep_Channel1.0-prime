"use strict";

const token = new URLSearchParams(location.search).get("token") || "";
const $ = (selector) => document.querySelector(selector);
const escapeHtml = (value) => String(value ?? "").replace(/[&<>"']/g, char =>
  ({"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;","'":"&#39;"})[char]);
const number = value => {
  const n = Number(value);
  if (!Number.isFinite(n) || String(value).trim() === "") throw Error("Enter a finite number");
  return n;
};
let meta, state, currentSpec, groups = [], points = {}, activeJob = null, selectedJob = null;
let resultPage = 0, lastResultKey = "", inspectParent = null;
const jobs = new Map();
const savedModels = {};

async function api(path, body) {
  const response = await fetch(path, {method: body === undefined ? "GET" : "POST",
    headers: {"X-App-Token": token, ...(body === undefined ? {} : {"Content-Type":"application/json"})},
    body: body === undefined ? undefined : JSON.stringify(body)});
  const data = await response.json();
  if (!response.ok) throw Error(data.error || `HTTP ${response.status}`);
  return data;
}
function notice(text) { $("#status").textContent = text; }
function error(exc) {
  const box = $("#toast"); box.textContent = exc.message || String(exc);
  box.classList.remove("hidden"); clearTimeout(error.timeout);
  error.timeout = setTimeout(() => box.classList.add("hidden"), 9000);
  notice("Error: " + box.textContent);
}
function fileUrl(jobId, relative) {
  return `/api/file/${jobId}/${relative.split("/").map(encodeURIComponent).join("/")}?token=${encodeURIComponent(token)}`;
}
function showTab(name) {
  if (state) {
    try {
      snapshotValues();
      if (name === "search") renderAxes();
    } catch (exc) {error(exc);return}
  }
  document.querySelectorAll(".panel").forEach(node => node.classList.toggle("visible", node.id === name));
  document.querySelectorAll("#tabs button").forEach(node => node.classList.toggle("selected", node.dataset.tab === name));
}
function chooseOptions(select, items, value) {
  select.innerHTML = items.map(item => `<option value="${escapeHtml(item)}">${escapeHtml(item)}</option>`).join("");
  if (items.includes(value)) select.value = value;
}

function copySearchEditsToValues() {
  const targets = new Map([...document.querySelectorAll("#parameters tbody tr")]
    .map(row => [row.dataset.key, row]));
  for (const row of document.querySelectorAll("#search-parameters tbody tr")) {
    const target = targets.get(row.dataset.key);
    if (!target) continue;
    for (const field of ["value", "low", "high", "dist", "points"])
      target.querySelector(`.${field}`).value = row.querySelector(`.${field}`).value;
  }
}
function snapshotValues() {
  if (!state) return;
  if ($("#search").classList.contains("visible")) copySearchEditsToValues();
  for (const row of document.querySelectorAll("#parameters tbody tr")) {
    const key = row.dataset.key;
    state.parameters[key] = number(row.querySelector(".value").value);
    const old = state.ranges[key];
    state.ranges[key] = [number(row.querySelector(".low").value),
      number(row.querySelector(".high").value), row.querySelector(".dist").value, old[3]];
    points[key] = number(row.querySelector(".points").value);
    if (!Number.isSafeInteger(points[key]) || points[key] < 1)
      throw Error(`${key}: draws / levels must be a positive whole number`);
  }
  for (const row of document.querySelectorAll("#fixed tbody tr"))
    state.fixed[row.dataset.key] = number(row.querySelector("input").value);
}
function displaySpec(spec, keepValues=false) {
  const old = state;
  currentSpec = spec;
  state = {role:spec.role, model:spec.model, composition:spec.composition,
    custom_modules:spec.custom_modules, parameters:{...spec.parameters}, fixed:{...spec.fixed},
    ranges:{...spec.ranges}, disabled_channels:[]};
  if (keepValues && old) {
    for (const key of Object.keys(state.parameters)) {
      if (key in old.parameters) state.parameters[key] = old.parameters[key];
      if (key in old.ranges) state.ranges[key] = old.ranges[key];
    }
    for (const key of Object.keys(state.fixed))
      if (key in old.fixed) state.fixed[key] = old.fixed[key];
    state.disabled_channels = old.disabled_channels.filter(key => key in spec.channels);
  }
  points = Object.fromEntries(Object.keys(state.parameters).map(key =>
    [key, keepValues && key in points ? points[key] : 100]));
  groups = [];
  $("#search-parameters tbody").replaceChildren();
  $("#role").value = spec.role;
  chooseOptions($("#model"), meta.roles[spec.role], spec.model);
  savedModels[spec.role] = spec.model;
  renderAll(spec);
}
async function loadModel() {
  const role = $("#role").value;
  const model = $("#model").value;
  displaySpec(await api("/api/spec", {role, model}));
  notice(`${role} / ${model} | Ready`);
}
async function reconfigure(config, keepValues=true, useModuleValues=false) {
  snapshotValues();
  const spec = await api("/api/spec", config);
  displaySpec(spec, keepValues);
  if (useModuleValues) {
    for (const module of spec.custom_modules) for (const parameter of module.parameters) {
      state.parameters[parameter.name] = spec.parameters[parameter.name];
      state.ranges[parameter.name] = spec.ranges[parameter.name];
    }
    renderParameters(spec);
    renderAxes();
  }
  notice("Model configuration updated; search combinations reset");
}
function renderAll(spec) {
  renderParameters(spec);
  renderFixed(spec);
  renderChannels(spec);
  renderBuilder(spec);
  renderModules();
  renderAxes();
  renderGroups();
  $("#formula").textContent = spec.formula;
  const content = $("#source-text");
  content.replaceChildren();
  for (const [title, value] of [["Equations", spec.sources.equations],
      ["Representative values",spec.sources.baseline],["Ranges and sampling",spec.sources.search]]) {
    const p = document.createElement("p");
    const strong = document.createElement("strong"); strong.textContent = title + ": ";
    p.append(strong, document.createTextNode(value)); content.append(p);
  }
  const scope = document.createElement("p");
  scope.textContent = "E/I is a research assignment, not a cell-type-specific fit. These are single-cell models. Experimental assemblies and custom currents are user definitions. SWO_candidate needs visual review.";
  content.append(scope);
  $("#basis").disabled = Boolean(state.composition);
  if (state.composition) $("#basis").value = "edited";
}
function renderParameters(spec) {
  const channels = spec.channels;
  $("#parameters tbody").innerHTML = Object.entries(state.parameters).map(([key,value]) => {
    const [lo, hi, dist, unit] = state.ranges[key];
    const off = state.disabled_channels.includes(key);
    return `<tr data-key="${escapeHtml(key)}" class="${off ? "off" : ""}"><td><b>${escapeHtml(key)}</b></td>
      <td><input class="value" type="number" step="any" value="${escapeHtml(value)}"></td>
      <td><input class="low" type="number" step="any" value="${escapeHtml(lo)}"></td>
      <td><input class="high" type="number" step="any" value="${escapeHtml(hi)}"></td>
      <td><select class="dist">${["log","uniform","neglog"].map(d => `<option value="${d}" ${d===dist?"selected":""}>${d}</option>`).join("")}</select></td>
      <td><input class="points" type="number" min="1" step="1" value="${points[key]}"></td>
      <td>${escapeHtml(unit)}</td><td>${JSON.stringify(state.ranges[key])===JSON.stringify(spec.ranges[key])?"Default bounds":"Edited"}</td>
      <td>${off?"Off":key in channels?"On":"Custom"}</td></tr>`;
  }).join("");
}
function renderFixed(spec) {
  $("#fixed tbody").innerHTML = Object.entries(state.fixed).map(([key,value]) =>
    `<tr data-key="${escapeHtml(key)}"><td><b>${escapeHtml(key)}</b></td><td><input type="number" step="any" value="${escapeHtml(value)}"></td><td>${escapeHtml(spec.fixed[key])}</td></tr>`).join("");
}
function renderChannels(spec) {
  $("#channel-list").innerHTML = Object.entries(spec.channels).map(([key,label]) =>
    `<div class="channel-card"><label><input type="checkbox" data-channel="${escapeHtml(key)}" ${state.disabled_channels.includes(key)?"":"checked"}>
      <span><b>${escapeHtml(label)}</b><br><small>${escapeHtml(key)}</small></span></label></div>`).join("");
}
function renderBuilder(spec) {
  const selected = state.composition?.channels || Object.fromEntries(Object.keys(spec.channels).map(key => [key,state.model]));
  $("#builder-table tbody").innerHTML = Object.entries(meta.channel_labels).map(([key,label]) =>
    `<tr data-key="${escapeHtml(key)}"><td><input class="use" type="checkbox" ${key in selected?"checked":""}></td>
    <td>${escapeHtml(label)} <small>(${escapeHtml(key)})</small></td><td><select class="source">${meta.sources[key].map(name =>
      `<option value="${escapeHtml(name)}" ${selected[key]===name?"selected":""}>${escapeHtml(name)}</option>`).join("")}</select></td></tr>`).join("");
}
function renderAxes() {
  const selected = new Set([...document.querySelectorAll("#search-parameters .axis:checked")].map(node => node.value));
  const channels = currentSpec.channels;
  $("#search-parameters tbody").innerHTML = Object.entries(state.parameters).map(([key,value]) => {
    const [lo,hi,dist,unit] = state.ranges[key];
    const off = state.disabled_channels.includes(key);
    return `<tr data-key="${escapeHtml(key)}" class="${off?"off":""}">
      <td><input class="axis" type="checkbox" value="${escapeHtml(key)}" ${selected.has(key)&&!off?"checked":""} ${off?"disabled":""}></td>
      <td><b>${escapeHtml(key)}</b></td>
      <td><input class="value" type="number" step="any" value="${escapeHtml(value)}"></td>
      <td><input class="low" type="number" step="any" value="${escapeHtml(lo)}"></td>
      <td><input class="high" type="number" step="any" value="${escapeHtml(hi)}"></td>
      <td><select class="dist">${["log","uniform","neglog"].map(d=>`<option value="${d}" ${d===dist?"selected":""}>${d}</option>`).join("")}</select></td>
      <td><input class="points" type="number" min="1" step="1" value="${points[key]}"></td>
      <td>${escapeHtml(unit)}</td><td>${off?"Off":key in channels?"On":"Custom"}</td>
      <td><button class="reset-row" type="button">Reset</button></td></tr>`;
  }).join("");
  preview();
}
function chosenAxes() { return [...document.querySelectorAll("#search-parameters .axis:checked")].map(node => node.value); }
function conditionCount(kind, keys, counts) {
  if (!keys.length) throw Error("Select one or more parameters");
  const values = keys.map(key => counts[key]);
  if (kind === "random" && new Set(values).size !== 1)
    throw Error("Random requires the same draw count for every selected parameter");
  const total = kind === "random" ? values[0] : values.reduce((a,b) => a*b, 1);
  if (!values.every(value => Number.isSafeInteger(value) && value >= 1))
    throw Error("Draws / levels must be positive whole numbers");
  if (!Number.isSafeInteger(total) || total < 1)
    throw Error("The condition count exceeds the browser's exact integer range");
  return total;
}
function preview() {
  try {
    const keys = chosenAxes(), kind = $("#kind").value;
    const current = {...points};
    for (const row of document.querySelectorAll("#search-parameters tbody tr")) current[row.dataset.key] = number(row.querySelector(".points").value);
    const total = conditionCount(kind, keys, current);
    const levels=keys.map(key=>current[key]);
    $("#preview").textContent = kind==="random"
      ? `${keys.join(", ")} | ${total.toLocaleString()} paired random draws = ${total.toLocaleString()} conditions`
      : `${keys.join(", ")} | ${levels.join(" × ")} = ${total.toLocaleString()} sweep conditions`;
  } catch (exc) { $("#preview").textContent = exc.message; }
}
function renderGroups() {
  $("#groups").innerHTML = groups.length ? groups.map((group,index) =>
    `<div class="group"><div><b>${escapeHtml(group.name)}</b> (${group.parameters.map(escapeHtml).join(", ")})
      <p>${escapeHtml(group.kind)}${group.kind==="sweep"?" · "+escapeHtml(group.basis):""} · ${group_total_local(group).toLocaleString()} conditions · ${group.parameters.map(key =>
      `${escapeHtml(key)} [${escapeHtml(group.ranges[key][0])}, ${escapeHtml(group.ranges[key][1])}]`).join("; ")}</p></div>
      <button data-remove-group="${index}">Remove</button></div>`).join("") : "<p>No search combinations queued.</p>";
}
function group_total_local(group) { return group.kind === "random" ? group.samples : group.parameters.reduce((n,key)=>n*group.points[key],1); }
function newModule() {
  return {name:"ChannelX",current:"gX * m^3 * (V - EX)",na_rate:"0",ca_rate:"0",
    variables:[{name:"m",kind:"instant",equation:"sigmoid((V - halfX) / slopeX)",initial:0}],
    parameters:[{name:"gX",value:0.1,low:0.01,high:100,distribution:"log",unit:"mS/cm²"},
      {name:"EX",value:55,low:40,high:70,distribution:"uniform",unit:"mV"},
      {name:"halfX",value:-35,low:-60,high:-10,distribution:"uniform",unit:"mV"},
      {name:"slopeX",value:7,low:2,high:15,distribution:"uniform",unit:"mV"}]};
}
function renderModules() {
  $("#module-list").innerHTML = state.custom_modules.map((module,i) =>
    `<div class="module-card" data-module="${i}"><div class="section-head"><h3>Current module ${i+1}</h3><button data-delete-module="${i}">Remove module</button></div>
    <div class="module-grid"><label>Name<input data-field="name" value="${escapeHtml(module.name)}"></label>
    <label>Current I (µA/cm²)<input data-field="current" value="${escapeHtml(module.current)}"></label>
    <label>Extra dNa/dt (mM/ms)<input data-field="na_rate" value="${escapeHtml(module.na_rate??"0")}"></label>
    <label>Extra dCa/dt (µM/ms)<input data-field="ca_rate" value="${escapeHtml(module.ca_rate??"0")}"></label></div>
    <h4>Variables <small>instant or ODE derivative / relaxation</small></h4>
    <div class="variable-rows">${(module.variables||[]).map((v,j)=>variableRow(v,i,j)).join("")}</div>
    <button data-add-variable="${i}">Add variable</button><h4>Searchable parameters</h4>
    <div class="parameter-rows">${(module.parameters||[]).map((p,j)=>parameterRow(p,i,j)).join("")}</div>
    <button data-add-parameter="${i}">Add parameter</button></div>`).join("") ||
    "<p>No custom currents. Add a module to enter a current, variables and searchable parameters.</p>";
}
function variableRow(v,i,j) {
  return `<div class="module-list-row" data-variable="${j}"><input data-field="name" placeholder="Name" value="${escapeHtml(v.name)}">
    <select data-field="kind"><option value="instant" ${v.kind==="instant"?"selected":""}>Instant</option><option value="ode" ${v.kind==="ode"?"selected":""}>ODE</option></select>
    <input data-field="equation" placeholder="Equation / target" value="${escapeHtml(v.equation)}">
    <input data-field="initial" type="number" step="any" title="ODE initial value" value="${escapeHtml(v.initial??0)}">
    <select data-field="ode_form"><option value="derivative" ${v.ode_form!=="relaxation"?"selected":""}>Derivative</option><option value="relaxation" ${v.ode_form==="relaxation"?"selected":""}>Relaxation</option></select>
    <input data-field="tau" placeholder="Tau (ms)" title="Relaxation tau expression" value="${escapeHtml(v.tau??"")}">
    <button data-delete-variable="${i}:${j}">×</button></div>`;
}
function parameterRow(p,i,j) {
  return `<div class="module-list-row params" data-parameter="${j}"><input data-field="name" placeholder="Name" value="${escapeHtml(p.name)}">
    <input data-field="value" type="number" step="any" title="Baseline" value="${escapeHtml(p.value)}">
    <input data-field="low" type="number" step="any" title="Lower" value="${escapeHtml(p.low)}">
    <input data-field="high" type="number" step="any" title="Upper" value="${escapeHtml(p.high)}">
    <select data-field="distribution">${["log","uniform","neglog"].map(d=>`<option ${p.distribution===d?"selected":""}>${d}</option>`).join("")}</select>
    <input data-field="unit" placeholder="Unit" value="${escapeHtml(p.unit??"")}">
    <button data-delete-parameter="${i}:${j}">×</button></div>`;
}
function collectModules() {
  return [...document.querySelectorAll(".module-card")].map(card => {
    const field = key => card.querySelector(`.module-grid [data-field="${key}"]`).value;
    return {name:field("name"),current:field("current"),na_rate:field("na_rate"),ca_rate:field("ca_rate"),
      variables:[...card.querySelectorAll("[data-variable]")].map(row => {
        const get = key => row.querySelector(`[data-field="${key}"]`).value;
        return {name:get("name"),kind:get("kind"),equation:get("equation"),
          initial:number(get("initial")),ode_form:get("ode_form"),tau:get("tau")};
      }),
      parameters:[...card.querySelectorAll("[data-parameter]")].map(row => {
        const get = key => row.querySelector(`[data-field="${key}"]`).value;
        return {name:get("name"),value:number(get("value")),low:number(get("low")),
          high:number(get("high")),distribution:get("distribution"),unit:get("unit")};
      })};
  });
}
function downloadJson(object,name) {
  const url = URL.createObjectURL(new Blob([JSON.stringify(object,null,2)+"\n"],{type:"application/json"}));
  const a=document.createElement("a");a.href=url;a.download=name;a.click();setTimeout(()=>URL.revokeObjectURL(url),1000);
}

async function startRun(mode) {
  snapshotValues();
  const body={...state,mode,groups,workers:number($("#workers").value)};
  const job=await api("/api/run",body);
  jobs.set(job.id,job); activeJob=job.id; selectedJob=job.id;
  showTab("results"); await poll();
}
async function poll() {
  try {
    const listing=await api("/api/jobs");
    for (const job of listing.jobs) jobs.set(job.id,job);
    const select=$("#job-select"), old=selectedJob;
    select.innerHTML=listing.jobs.map(job=>`<option value="${job.id}">${escapeHtml(job.output)} · ${escapeHtml(job.mode)} · ${escapeHtml(job.state)}</option>`).join("");
    if (selectedJob && jobs.has(selectedJob)) select.value=selectedJob;
    else if (listing.jobs.length) selectedJob=select.value;
    const current=jobs.get(activeJob)||jobs.get(selectedJob);
    const running=listing.jobs.some(job=>job.state==="running");
    $("#baseline").disabled=running;$("#run-search").disabled=running;$("#stop").disabled=!running;
    if (current) {
      $("#progress").value=current.total?100*current.completed/current.total:0;
      $("#progress-text").textContent=`${current.completed.toLocaleString()} / ${current.total.toLocaleString()}`;
      notice(`${current.message}${current.last_label?" · "+current.last_label:""}${current.error?" · "+current.error:""}`);
      if (current.state!=="running" && activeJob===current.id) {
        if (current.mode==="inspect" && current.state==="done" && inspectParent) {
          const parent=jobs.get(inspectParent);
          const relative=current.trace.slice(parent.output.length+1);
          showPdf(fileUrl(inspectParent,relative));
          lastResultKey="";
        } else if (current.mode==="baseline" && current.state==="done") showPdf(fileUrl(current.id,"trace.pdf"));
        activeJob=null;
      }
    }
    if (selectedJob && (selectedJob!==old || !running)) await renderResults();
  } catch(exc) {error(exc)}
  clearTimeout(poll.timer);poll.timer=setTimeout(poll,1000);
}
async function renderResults() {
  const job=jobs.get(selectedJob);if(!job)return;
  $("#output-location").textContent=`Results on Linux: ${meta.output_root}/${job.output}`;
  const label=$("#label-filter").value;
  const key=`${job.id}:${label}:${resultPage}:${job.state}:${job.completed}`;
  if(key===lastResultKey)return;
  const link=$("#summary-link");link.classList.toggle("hidden",job.mode!=="search");
  const configLink=$("#config-link");
  configLink.classList.toggle("hidden",job.mode==="inspect"||job.state==="running");
  if(job.mode!=="inspect") configLink.href=fileUrl(job.id,
    job.mode==="search"?"search_config.json":"config_metrics.json");
  if(job.mode!=="search"){
    $("#result-table tbody").replaceChildren();$("#result-count").textContent=job.message;
    $("#prev-page").disabled=true;$("#next-page").disabled=true;
    if(job.state==="done" && job.trace) {
      if(job.mode==="baseline") showPdf(fileUrl(job.id,"trace.pdf"));
      else {
        const parent=[...jobs.values()].find(candidate=>candidate.mode==="search" &&
          job.trace.startsWith(candidate.output+"/"));
        if(parent) showPdf(fileUrl(parent.id,job.trace.slice(parent.output.length+1)));
      }
    }
    lastResultKey=key;
    return;
  }
  link.href=fileUrl(job.id,"summary.csv");
  const data=await api(`/api/results/${job.id}?label=${encodeURIComponent(label)}&offset=${resultPage*100}`);
  lastResultKey=key;
  $("#result-count").textContent=`${data.total.toLocaleString()} matching results · page ${resultPage+1}`;
  $("#prev-page").disabled=resultPage===0;
  $("#next-page").disabled=(resultPage+1)*100>=data.total;
  $("#result-table tbody").innerHTML=data.rows.map((row,i)=>
    `<tr><td>${escapeHtml(row.group)}</td><td>${escapeHtml(row.index)}</td><td>${escapeHtml(row.label)}</td>
     <td>${escapeHtml(row.peak_hz)}</td><td>${escapeHtml(row.spikes_per_s)}</td><td>${escapeHtml(row.choice_indices)}</td>
     <td><button data-row="${i}">${row.trace_path?"Open PDF":"Compute trace"}</button></td></tr>`).join("");
  renderResults.rows=data.rows;
}
function showPdf(url) {
  if($("#pdf-view").src!==new URL(url,location.href).href)$("#pdf-view").src=url;
  $("#trace-download").href=url;
  $("#trace-download").classList.remove("hidden");
  $("#pdf-view").classList.remove("hidden");
  $("#trace-area > p").classList.add("hidden");
}

function events() {
  $("#tabs").addEventListener("click", event => {const tab=event.target.closest("[data-tab]");if(tab)showTab(tab.dataset.tab)});
  $("#role").addEventListener("change",async()=>{try{const role=$("#role").value;
    chooseOptions($("#model"),meta.roles[role],savedModels[role]||meta.roles[role][0]);await loadModel()
  }catch(exc){error(exc)}});
  $("#model").addEventListener("change",async()=>{try{await loadModel()}catch(exc){error(exc)}});
  $("#reset").addEventListener("click",async()=>{try{await loadModel()}catch(exc){error(exc)}});
  $("#baseline").addEventListener("click",async()=>{try{await startRun("baseline")}catch(exc){error(exc)}});
  $("#channel-list").addEventListener("change",async event=>{
    if(!event.target.matches("[data-channel]"))return;
    try{snapshotValues();const key=event.target.dataset.channel;
      state.disabled_channels=event.target.checked?state.disabled_channels.filter(k=>k!==key):[...state.disabled_channels,key];
      const spec=await api("/api/spec",state);$("#formula").textContent=spec.formula;
      renderParameters(spec);
      if(!event.target.checked)groups=groups.filter(group=>!group.parameters.includes(key));
      renderAxes();renderGroups();
    }catch(exc){error(exc)}
  });
  $("#enable-all-channels").addEventListener("click",async()=>{try{
    snapshotValues();state.disabled_channels=[];
    const spec=await api("/api/spec",state);$("#formula").textContent=spec.formula;
    renderChannels(spec);renderParameters(spec);renderAxes();notice("All channels on");
  }catch(exc){error(exc)}});
  $("#apply-builder").addEventListener("click",async()=>{try{
    const channels={};for(const row of document.querySelectorAll("#builder-table tbody tr"))
      if(row.querySelector(".use").checked)channels[row.dataset.key]=row.querySelector(".source").value;
    await reconfigure({...state,composition:{channels}},false);
  }catch(exc){error(exc)}});
  $("#use-published").addEventListener("click",async()=>{try{await reconfigure({...state,composition:null},false)}catch(exc){error(exc)}});
  $("#add-module").addEventListener("click",()=>{try{state.custom_modules=collectModules();state.custom_modules.push(newModule());renderModules()}catch(exc){error(exc)}});
  $("#module-list").addEventListener("click",event=>{try{
    const button=event.target.closest("button");if(!button)return;
    state.custom_modules=collectModules();let item;
    if(button.dataset.deleteModule!==undefined)state.custom_modules.splice(Number(button.dataset.deleteModule),1);
    else if(button.dataset.addVariable!==undefined)state.custom_modules[Number(button.dataset.addVariable)].variables.push({name:"gate",kind:"ode",equation:"sigmoid((V + 40)/6)",initial:0,ode_form:"relaxation",tau:"10"});
    else if(button.dataset.addParameter!==undefined)state.custom_modules[Number(button.dataset.addParameter)].parameters.push({name:"tauGate",value:10,low:1,high:100,distribution:"log",unit:"ms"});
    else if(button.dataset.deleteVariable){item=button.dataset.deleteVariable.split(":").map(Number);state.custom_modules[item[0]].variables.splice(item[1],1)}
    else if(button.dataset.deleteParameter){item=button.dataset.deleteParameter.split(":").map(Number);state.custom_modules[item[0]].parameters.splice(item[1],1)}
    renderModules();
  }catch(exc){error(exc)}});
  $("#apply-modules").addEventListener("click",async()=>{try{
    await reconfigure({...state,custom_modules:collectModules()},true,true);
  }catch(exc){error(exc)}});
  $("#save-modules").addEventListener("click",()=>{try{
    snapshotValues();const modules=collectModules();for(const module of modules)for(const param of module.parameters){
      const key=param.name;if(key in state.parameters){param.value=state.parameters[key];
        [param.low,param.high,param.distribution,param.unit]=state.ranges[key]}}
    downloadJson({format:"ChannelCircuitLab.custom.v1",base_model:state.model,modules},"custom_currents.json");
  }catch(exc){error(exc)}});
  $("#load-modules").addEventListener("change",async event=>{try{
    const payload=JSON.parse(await event.target.files[0].text());
    if(payload.format!=="ChannelCircuitLab.custom.v1"||!Array.isArray(payload.modules))throw Error("Unsupported module JSON");
    await reconfigure({...state,custom_modules:payload.modules},true,true);
  }catch(exc){error(exc)}finally{event.target.value=""}});
  $("#search-parameters").addEventListener("change",preview);
  $("#search-parameters").addEventListener("input",event=>{if(event.target.classList.contains("points"))preview()});
  $("#search-parameters").addEventListener("click",event=>{const button=event.target.closest(".reset-row");if(!button)return;
    try{const row=button.closest("tr"),key=row.dataset.key,[lo,hi,dist]=currentSpec.ranges[key];
      for(const [field,value] of [["value",currentSpec.parameters[key]],["low",lo],["high",hi],["dist",dist],["points",100]])
        row.querySelector(`.${field}`).value=value;
      snapshotValues();
      renderParameters(currentSpec);renderAxes();notice(`${key}: default value and bounds restored`);
    }catch(exc){error(exc)}});
  $("#kind").addEventListener("change",preview);
  $("#apply-all").addEventListener("click",()=>{try{
    snapshotValues();const count=number($("#all-points").value);
    if(!Number.isSafeInteger(count)||count<1)throw Error("Enter a positive whole number");
    const updated=groups.map(group=>{const p=Object.fromEntries(group.parameters.map(key=>[key,count]));
      const samples=conditionCount(group.kind,group.parameters,p);return {...group,points:p,samples}});
    if(!Number.isSafeInteger(updated.reduce((sum,group)=>sum+group_total_local(group),0)))
      throw Error("The queued condition count exceeds the browser's exact integer range");
    groups=updated;points=Object.fromEntries(Object.keys(points).map(key=>[key,count]));
    renderParameters(currentSpec);renderAxes();
    renderGroups();preview();notice(`Applied ${count} draws / levels to all parameters and searches`);
  }catch(exc){error(exc)}});
  $("#add-group").addEventListener("click",()=>{try{
    snapshotValues();const axes=chosenAxes(),kind=$("#kind").value,basis=$("#basis").value;
    if(axes.some(key=>state.disabled_channels.includes(key)))throw Error("Enable an off channel before searching it");
    if(kind==="sweep"&&basis==="paper"&&(state.composition||axes.some(key=>state.custom_modules.some(module=>module.parameters.some(p=>p.name===key)))))
      throw Error("Experimental assemblies and custom parameters use edited sweep bounds");
    const samples=conditionCount(kind,axes,points);
    const next=groups.length+1;let index=next;while(groups.some(group=>group.name===`group_${index}`))index++;
    const selected=Object.fromEntries(axes.map(key=>[key,points[key]]));
    groups.push({name:`group_${index}`,kind,basis,samples,parameters:axes,points:selected,
      ranges:Object.fromEntries(axes.map(key=>[key,[...state.ranges[key]]] ))});
    if(!Number.isSafeInteger(groups.reduce((sum,g)=>sum+group_total_local(g),0))){
      groups.pop();throw Error("The queued condition count exceeds the browser's exact integer range")}
    renderGroups();notice(`${samples.toLocaleString()} conditions added`);
  }catch(exc){error(exc)}});
  $("#groups").addEventListener("click",event=>{const b=event.target.closest("[data-remove-group]");if(b){groups.splice(Number(b.dataset.removeGroup),1);renderGroups()}});
  $("#run-search").addEventListener("click",async()=>{try{await startRun("search")}catch(exc){error(exc)}});
  $("#stop").addEventListener("click",async()=>{try{const id=activeJob||[...jobs.values()].find(j=>j.state==="running")?.id;if(id)await api("/api/stop",{id})}catch(exc){error(exc)}});
  $("#job-select").addEventListener("change",async event=>{selectedJob=event.target.value;resultPage=0;lastResultKey="";try{await renderResults()}catch(exc){error(exc)}});
  $("#label-filter").addEventListener("change",async()=>{resultPage=0;lastResultKey="";try{await renderResults()}catch(exc){error(exc)}});
  for(const [id,delta] of [["#prev-page",-1],["#next-page",1]])$(id).addEventListener("click",async()=>{resultPage+=delta;lastResultKey="";try{await renderResults()}catch(exc){error(exc)}});
  $("#result-table").addEventListener("click",async event=>{const b=event.target.closest("[data-row]");if(!b)return;
    try{const row=renderResults.rows[Number(b.dataset.row)];if(row.trace_path)showPdf(fileUrl(selectedJob,`${row.trace_path}/trace.pdf`));
      else{inspectParent=selectedJob;const job=await api("/api/inspect",{job:selectedJob,group:row.group,index:row.index});
        jobs.set(job.id,job);activeJob=job.id;await poll()}}
    catch(exc){error(exc)}});
}

async function initialize() {
  if(!token){error(Error("Open the complete URL printed by the Linux server, including its token"));return}
  try{
    meta=await api("/api/bootstrap");
    chooseOptions($("#role"),Object.keys(meta.roles),"E");
    chooseOptions($("#model"),meta.roles.E,"Sato 2025 NAN");
    $("#workers").max=meta.cpus;$("#workers").value=meta.default_workers;
    $("#cpu-note").textContent=`${meta.cpus} logical CPUs available`;
    displaySpec(meta.default);events();poll();
  }catch(exc){error(exc)}
}
initialize();
