"""Static assets for the local dashboard: the white+purple design system and the
interaction JS (hover tooltip, view toggles). Inlined so the site runs fully offline
(no CDN) — important for a possible Raspberry Pi move."""

from __future__ import annotations

# Light-first, clean white with purple accents (per the user's brief). Dark is provided
# but secondary. Purple is the single accent; semantic colors are reserved and separate.
CSS = """
:root{
  --plane:#f6f3fc; --surface:#ffffff; --surface-2:#f5f1fd; --inset:#faf8ff;
  --ink:#1b1630; --ink-2:#574f6e; --muted:#938ca6; --line:#ebe5f7;
  --grid:#efeafa; --axis:#d8cfee;
  --accent:#6d43e8; --accent-strong:#5731cf; --accent-weak:#efeafd; --accent-soft:#e2d8fb;
  --seq-1:#cbbcf6; --seq-2:#a488ef; --seq-3:#6d43e8; --seq-4:#4a27ac;
  --good:#12a150; --good-weak:#e2f5ea; --warn:#dd8a0b; --warn-weak:#fbf0dc;
  --alert:#e0483f; --alert-weak:#fbe6e4;
  --easy:#6d43e8; --mod:#dd8a0b; --hard:#e0483f; --violet:#7c4dff; --teal:#0e9c8a;
  --shadow:0 1px 2px rgba(27,22,48,.04),0 4px 16px rgba(27,22,48,.05);
}
:root[data-theme="dark"]{
  --plane:#131019; --surface:#1b1726; --surface-2:#221c31; --inset:#191423;
  --ink:#f1ecfb; --ink-2:#c3bad9; --muted:#8f87a6; --line:#2b2440;
  --grid:#241d36; --axis:#3a3155;
  --accent:#8b6bf0; --accent-strong:#a68cf5; --accent-weak:#241c3a; --accent-soft:#33285a;
  --seq-1:#3a2d63; --seq-2:#5a41a6; --seq-3:#8b6bf0; --seq-4:#b9a3f7;
  --good:#2bb668; --good-weak:#16301f; --warn:#e0a53a; --warn-weak:#2f2410;
  --alert:#ef6a62; --alert-weak:#33191a;
  --easy:#8b6bf0; --mod:#e0a53a; --hard:#ef6a62; --violet:#a68cf5; --teal:#2bb6a2;
  --shadow:0 1px 2px rgba(0,0,0,.3);
}
*{box-sizing:border-box}
html,body{margin:0}
body{background:var(--plane);color:var(--ink);
  font-family:system-ui,-apple-system,"Segoe UI",sans-serif;line-height:1.55;
  -webkit-font-smoothing:antialiased}
.mono{font-family:ui-monospace,"SF Mono","Cascadia Code","Consolas",monospace;
  font-variant-numeric:tabular-nums}
a{color:inherit;text-decoration:none}

/* ---- shell: sidebar + main ---- */
.shell{display:grid;grid-template-columns:232px 1fr;min-height:100vh}
.side{background:var(--surface);border-right:1px solid var(--line);padding:22px 16px;
  position:sticky;top:0;height:100vh;display:flex;flex-direction:column;gap:4px}
.brand{display:flex;align-items:center;gap:10px;padding:4px 8px 20px}
.brand .mark{width:30px;height:30px;border-radius:9px;background:var(--accent);
  display:grid;place-items:center;color:#fff;font-weight:800;font-size:16px}
.brand b{font-size:16px;letter-spacing:-.01em}
.nav a{display:flex;align-items:center;gap:11px;padding:9px 12px;border-radius:10px;
  color:var(--ink-2);font-weight:550;font-size:14.5px}
.nav a:hover{background:var(--accent-weak);color:var(--ink)}
.nav a.on{background:var(--accent-weak);color:var(--accent-strong)}
.nav a .ic{width:9px;height:9px;border-radius:3px;background:currentColor;opacity:.55}
.nav a.on .ic{opacity:1}
.side .foot{margin-top:auto;color:var(--muted);font-size:11.5px;padding:8px}
.main{padding:clamp(20px,3.5vw,40px) clamp(16px,3.5vw,44px);max-width:1080px}

/* ---- headers ---- */
.eyebrow{font-size:11.5px;letter-spacing:.15em;text-transform:uppercase;
  color:var(--accent);font-weight:700;margin:0 0 6px}
h1{font-size:clamp(24px,3.4vw,32px);letter-spacing:-.02em;margin:0 0 6px;font-weight:740;
  text-wrap:balance}
h2{font-size:19px;letter-spacing:-.01em;margin:0 0 3px;font-weight:680}
.sub{color:var(--ink-2);font-size:15px;margin:0 0 8px;max-width:60ch}
.pagehead{margin-bottom:22px}

/* ---- cards / grid ---- */
.grid{display:grid;gap:16px}
.g2{grid-template-columns:repeat(2,1fr)} .g3{grid-template-columns:repeat(3,1fr)}
.g4{grid-template-columns:repeat(4,1fr)}
.card{background:var(--surface);border:1px solid var(--line);border-radius:16px;
  padding:18px 20px;box-shadow:var(--shadow)}
.card h2{margin-bottom:10px}
.card .hint{color:var(--muted);font-size:12.5px;margin:2px 0 0}
.span2{grid-column:span 2}

/* ---- gauge (number with a scale + label) ---- */
.gauge .glabel{font-size:12px;letter-spacing:.04em;text-transform:uppercase;
  color:var(--muted);font-weight:600}
.gauge .grow{display:flex;align-items:baseline;gap:8px;margin:2px 0 3px}
.gauge .gval{font-size:30px;font-weight:760;letter-spacing:-.02em}
.gauge .gtag{font-size:12.5px;font-weight:650;padding:2px 9px;border-radius:20px}
.gtag.t-good{background:var(--good-weak);color:var(--good)}
.gtag.t-warn{background:var(--warn-weak);color:var(--warn)}
.gtag.t-alert{background:var(--alert-weak);color:var(--alert)}
.gtag.t-accent{background:var(--accent-weak);color:var(--accent-strong)}
.gauge .track{position:relative;height:8px;border-radius:6px;margin:12px 0 6px;overflow:visible}
.gauge .fill{position:absolute;top:0;bottom:0;left:0;border-radius:6px}
.gauge .mk{position:absolute;top:50%;width:14px;height:14px;border-radius:50%;
  background:var(--surface);border:3px solid var(--ink);transform:translate(-50%,-50%);
  box-shadow:0 1px 3px rgba(0,0,0,.2)}
.gauge .peak{position:absolute;top:50%;width:2px;height:14px;background:var(--muted);
  transform:translate(-50%,-50%);border-radius:2px}
.gauge .ends{display:flex;justify-content:space-between;color:var(--muted);font-size:11.5px}
.gauge .gnote{color:var(--ink-2);font-size:12.5px;margin-top:6px}

/* ---- figures / charts ---- */
.fig{margin-top:10px}
.chart{width:100%;height:auto;display:block;overflow:visible}
.legend{display:flex;gap:14px;flex-wrap:wrap;font-size:12.5px;color:var(--ink-2);
  margin:2px 2px 8px;align-items:center}
.legend b{color:var(--ink);font-weight:600}
.key{width:10px;height:10px;border-radius:3px;display:inline-block;margin-right:6px;
  vertical-align:-1px}
.cap{font-size:12px;color:var(--muted);margin:8px 2px 0}
.grid-l{stroke:var(--grid);stroke-width:1}
.axis-l{stroke:var(--axis);stroke-width:1.3}
.dash{stroke:var(--axis);stroke-width:1;stroke-dasharray:3 4}
.tk{fill:var(--muted);font-size:11px;font-family:ui-monospace,monospace}
.tk-r{text-anchor:end} .tk-m{text-anchor:middle}
.note{fill:var(--ink-2);font-size:11px;font-weight:600}
.note-al{fill:var(--alert)}
.hoverable{cursor:pointer;transition:opacity .1s}
.hoverable:hover{opacity:.78}

/* ---- month nav (weekly volume) ---- */
.mnav-bar{display:inline-flex;align-items:center;gap:10px}
.mnav{display:inline-flex;width:28px;height:28px;align-items:center;justify-content:center;
  border-radius:8px;background:var(--surface-2);color:var(--ink-2);text-decoration:none;
  font-size:16px;font-weight:700;line-height:1}
.mnav:hover{background:var(--accent-weak);color:var(--accent-strong)}
.mnav.off{opacity:.35;pointer-events:none}
.mnav-lbl{font-weight:640;font-size:14.5px;min-width:118px;text-align:center}

/* ---- toggle ---- */
.seg{display:inline-flex;background:var(--surface-2);border:1px solid var(--line);
  border-radius:10px;padding:3px;gap:2px}
.seg button{border:0;background:transparent;color:var(--ink-2);font:inherit;font-size:13px;
  font-weight:600;padding:5px 14px;border-radius:8px;cursor:pointer}
.seg button.on{background:var(--surface);color:var(--accent-strong);box-shadow:var(--shadow)}

/* ---- tables ---- */
.tbl{width:100%;border-collapse:collapse;font-size:13px}
.tbl th{text-align:left;color:var(--muted);font-weight:600;font-size:11px;
  text-transform:uppercase;letter-spacing:.05em;padding:8px 10px;border-bottom:1px solid var(--line)}
.tbl td{padding:8px 10px;border-bottom:1px solid var(--line)}
.tbl .num{text-align:right}
.tbl tbody tr:hover{background:var(--surface-2)}
.pz{font-size:11px;padding:1px 9px;border-radius:20px;font-weight:600;white-space:nowrap}
.pz-easy{background:var(--accent-weak);color:var(--accent-strong)}
.pz-mod{background:var(--warn-weak);color:var(--warn)}
.pz-hard{background:var(--alert-weak);color:var(--alert)}
.pz-na{background:var(--surface-2);color:var(--muted)}

/* ---- plan strip ---- */
.plan{display:grid;grid-template-columns:repeat(7,1fr);gap:8px}
.pday{background:var(--surface-2);border:1px solid var(--line);border-radius:11px;
  padding:11px 8px;text-align:center}
.pday .pd{font-size:11px;color:var(--muted);text-transform:uppercase;letter-spacing:.04em}
.pday .pt{font-size:13.5px;font-weight:650;margin-top:4px}
.pday .pk{font-size:11px;color:var(--ink-2)}
.pday.rest .pt{color:var(--muted);font-weight:500}
.pday.run{border-color:var(--accent-soft);background:var(--accent-weak)}
.pday.done{border-color:var(--good)}
.pday.cross{border-color:var(--teal);background:var(--good-weak)}
.pday.cross .pt{color:var(--teal);font-weight:650}
.pday.future{border-style:dashed;background:transparent}
.pday.future .pt,.pday.future .pk{color:var(--muted)}

/* ---- sync feedback banner ---- */
.banner{display:flex;align-items:flex-start;gap:12px;padding:13px 16px;border-radius:14px;
  border:1px solid var(--line);margin-bottom:18px;box-shadow:var(--shadow)}
.banner .bico{width:9px;height:9px;border-radius:50%;margin-top:6px;flex:none}
.banner .btext{display:flex;flex-direction:column;gap:1px}
.banner .btext b{font-weight:680;font-size:14px}
.banner .btext span{color:var(--ink-2);font-size:12.5px}
.banner .bx{margin-left:auto;align-self:center;background:transparent;border:0;
  color:var(--muted);font-size:18px;line-height:1;cursor:pointer;padding:0 4px}
.banner .bx:hover{color:var(--ink)}
.b-ok{background:var(--good-weak)} .b-ok .bico{background:var(--good)}
.b-warn{background:var(--warn-weak)} .b-warn .bico{background:var(--warn)}
.b-err{background:var(--alert-weak)} .b-err .bico{background:var(--alert)}

/* ---- sync tab ---- */
.gtag{display:inline-block;font-size:12.5px;font-weight:650;padding:2px 9px;border-radius:20px;
  white-space:nowrap}
.syncstate{display:flex;justify-content:space-between;align-items:center;gap:16px;flex-wrap:wrap}
.syncstate .ss-head{display:flex;align-items:center;gap:12px}
.syncstate .ss-dot{width:12px;height:12px;border-radius:50%;flex:none}
.syncstate h2{margin:0}
.syncstate .ss-actions{display:flex;gap:8px;flex-wrap:wrap}
.pill.solid{background:var(--accent);color:#fff}
.pill.ghost{background:transparent;border:1px solid var(--line);color:var(--ink-2)}
.pill.ghost:hover{border-color:var(--accent-soft);color:var(--ink)}
.bf{display:inline-flex;align-items:center;gap:6px;flex-wrap:wrap}
.bf-lbl{font-size:12.5px;color:var(--muted)}
.synccard .sc-label{font-size:12px;letter-spacing:.04em;text-transform:uppercase;
  color:var(--muted);font-weight:600}
.synccard .sc-row{display:flex;align-items:baseline;gap:8px;margin:4px 0 2px}
.synccard .sc-val{font-size:22px;font-weight:720;letter-spacing:-.01em}
.synccard .sc-sub{color:var(--ink-2);font-size:12.5px}
.hm{display:grid;grid-template-columns:repeat(7,1fr);gap:5px;margin-top:10px;max-width:340px}
.hm-wd{font-size:10.5px;color:var(--muted);text-align:center;padding-bottom:2px}
.hm-cell{aspect-ratio:1;border-radius:5px;background:var(--surface-2)}
.hm-full{background:var(--seq-3)} .hm-partial{background:var(--seq-1)}
.hm-missing{background:var(--surface-2);border:1px solid var(--line)}
.hm-pad{background:transparent}
.hm-cell[data-tip]{cursor:pointer}
.hm-cell[data-tip]:hover{outline:2px solid var(--accent);outline-offset:1px}

/* ---- race predictions ---- */
.rgrid{display:grid;grid-template-columns:repeat(4,1fr);gap:10px;margin-top:12px}
.rtile{background:var(--surface-2);border:1px solid var(--line);border-radius:12px;
  padding:12px 14px;text-align:center}
.rtile .rt-d{font-size:12px;color:var(--muted);text-transform:uppercase;letter-spacing:.04em;
  font-weight:600}
.rtile .rt-t{font-size:22px;font-weight:740;letter-spacing:-.01em;margin-top:2px}
.rtile .rt-dn{display:inline-block;font-size:11.5px;font-weight:650;margin-top:2px}
.rt-dn.good{color:var(--good)} .rt-dn.warn{color:var(--warn)} .rt-dn.muted{color:var(--muted)}
.spark{width:100%;height:26px;display:block;margin-top:6px}

/* ---- sleep detail panel ---- */
.sleep-wrap{display:grid;grid-template-columns:1fr 210px;gap:16px;align-items:start;margin-top:8px}
.sleep-wrap .fig{margin-top:0}
.sleep-detail{background:var(--surface-2);border:1px solid var(--line);border-radius:12px;
  padding:14px 16px}
.sleep-detail .sd-date{font-size:12px;color:var(--muted);text-transform:uppercase;
  letter-spacing:.04em;font-weight:600}
.sleep-detail .sd-total{font-size:16px;margin:2px 0 10px}
.sleep-detail .sd-rows{display:flex;flex-direction:column;gap:6px}
.sleep-detail .sd-row{display:flex;justify-content:space-between;align-items:center;font-size:13px}
.sleep-detail .sd-k{display:flex;align-items:center;gap:7px;color:var(--ink-2)}
.sleep-detail .sd-v{font-variant-numeric:tabular-nums;font-weight:600}
.sleep-detail .sw{width:10px;height:10px;border-radius:3px;display:inline-block;flex:none}
.sw.s-deep{background:var(--seq-4)} .sw.s-light{background:var(--seq-2)}
.sw.s-rem{background:var(--teal)} .sw.s-awake{background:var(--muted)}
.sleep-detail .sd-hr{margin-top:10px;padding-top:10px;border-top:1px solid var(--line);font-size:13px}
.sleep-detail .sd-min{color:var(--muted)}

/* ---- plan detail (weekly plan with notes) ---- */
.wk-list{display:flex;flex-direction:column;gap:8px;margin-top:10px}
.wk-row{display:flex;gap:14px;padding:11px 14px;border:1px solid var(--line);
  border-radius:12px;background:var(--surface-2)}
.wk-day{flex:none;width:60px;font-size:12px;color:var(--muted);font-weight:650;
  text-transform:uppercase;letter-spacing:.03em;padding-top:3px}
.wk-body{flex:1;min-width:0}
.wk-head{display:flex;align-items:center;gap:9px;flex-wrap:wrap}
.wk-badge{font-size:12px;font-weight:650;padding:2px 10px;border-radius:20px;white-space:nowrap}
.wk-run{background:var(--accent-weak);color:var(--accent-strong)}
.wk-support{background:var(--good-weak);color:var(--teal)}
.wk-quality{background:var(--warn-weak);color:var(--warn)}
.wk-rest{background:var(--surface);color:var(--muted);border:1px solid var(--line)}
.wk-metric{display:flex;flex-wrap:wrap;align-items:baseline;margin-top:6px}
.wk-m{font-size:13.5px;color:var(--ink);font-weight:640;font-variant-numeric:tabular-nums;
  padding:0 13px;border-left:1px solid var(--line);white-space:nowrap}
.wk-m:first-child{padding-left:0;border-left:0}
.wk-mk{font-size:10px;color:var(--muted);text-transform:uppercase;letter-spacing:.05em;
  font-weight:700;margin-right:6px}
.wk-done{font-size:11.5px;color:var(--good);font-weight:650}
.wk-note{font-size:12.5px;color:var(--ink-2);margin-top:5px;line-height:1.45}

/* ---- collapsible (all runs) ---- */
.allruns{background:var(--surface);border:1px solid var(--line);border-radius:16px;
  padding:14px 20px;box-shadow:var(--shadow)}
.allruns>summary{cursor:pointer;font-weight:680;font-size:15px;color:var(--ink);
  list-style:none;display:flex;align-items:center;gap:8px}
.allruns>summary::-webkit-details-marker{display:none}
.allruns>summary::before{content:"\25B8";color:var(--accent);font-size:12px}
.allruns[open]>summary::before{content:"\25BE"}

/* ---- misc ---- */
.pill{background:var(--surface-2);color:var(--ink-2);border-radius:20px;padding:2px 10px;
  font-size:12px;font-weight:600}
.pill[disabled]{opacity:.65;cursor:progress}
.alertrow{display:flex;gap:12px;align-items:flex-start;padding:12px 14px;border-radius:12px;
  background:var(--surface-2);border:1px solid var(--line);font-size:13.5px}
.alertrow .dot{width:8px;height:8px;border-radius:50%;margin-top:6px;flex:none}
.empty{color:var(--muted);font-size:14px;padding:8px 2px}
.rlist{display:flex;flex-direction:column;gap:8px}
.ritem{display:flex;justify-content:space-between;align-items:center;padding:12px 16px;
  border:1px solid var(--line);border-radius:12px;background:var(--surface)}
.ritem:hover{border-color:var(--accent-soft)}

#tip{position:fixed;z-index:50;pointer-events:none;background:var(--ink);color:var(--surface);
  font-size:12.5px;padding:8px 11px;border-radius:9px;opacity:0;transition:opacity .08s;
  max-width:260px;line-height:1.5;box-shadow:0 6px 20px rgba(0,0,0,.24)}
#tip .mono{font-variant-numeric:tabular-nums}
#tip b{font-weight:680}

@media(max-width:820px){
  .shell{grid-template-columns:1fr}
  .side{position:static;height:auto;flex-direction:row;flex-wrap:wrap;align-items:center;
    gap:4px;padding:12px}
  .side .brand{padding:4px 8px 4px} .side .foot{display:none}
  .nav{display:flex;flex-wrap:wrap;gap:2px}
  .g4,.g3,.g2{grid-template-columns:1fr 1fr} .span2{grid-column:span 2}
}
@media(max-width:640px){.sleep-wrap{grid-template-columns:1fr}}
@media(max-width:520px){.g4,.g3,.g2{grid-template-columns:1fr}.span2{grid-column:span 1}
  .plan,.rgrid{grid-template-columns:repeat(2,1fr)}}
"""

# Hover tooltip (any element with data-tip), and simple client-side view toggles.
JS = """
(function(){
  var tip=document.createElement('div'); tip.id='tip'; document.body.appendChild(tip);
  function show(e){var t=e.currentTarget.getAttribute('data-tip'); if(!t)return;
    tip.innerHTML=t; tip.style.opacity='1'; move(e);}
  function move(e){var p=12; var w=tip.offsetWidth,h=tip.offsetHeight;
    var x=e.clientX+p, y=e.clientY+p;
    if(x+w>innerWidth) x=e.clientX-w-p; if(y+h>innerHeight) y=e.clientY-h-p;
    tip.style.left=x+'px'; tip.style.top=y+'px';}
  function hide(){tip.style.opacity='0';}
  function bind(){document.querySelectorAll('[data-tip]').forEach(function(el){
    el.addEventListener('mouseenter',show); el.addEventListener('mousemove',move);
    el.addEventListener('mouseleave',hide);});}
  document.addEventListener('DOMContentLoaded',bind);

  // sync buttons: show progress while the (synchronous) daily cycle runs
  document.querySelectorAll('form[data-sync]').forEach(function(sf){
    sf.addEventListener('submit',function(){
      var b=sf.querySelector('button'); if(!b)return;
      setTimeout(function(){b.disabled=true;
        var t=b.getAttribute('data-progress'); if(t)b.textContent=t;},0);
    });
  });

  // sleep chart: hovering a night updates the fixed detail panel beside it
  var sdp=document.getElementById('sleep-detail');
  function sleepPanel(d){
    function row(cls,k,v){return '<div class="sd-row"><span class="sd-k"><i class="sw '+cls
      +'"></i>'+k+'</span><span class="sd-v">'+v+'</span></div>';}
    return '<div class="sd-date">'+d.date+'</div><div class="sd-total">dormiu <b>'+d.total
      +'</b></div><div class="sd-rows">'+row('s-deep','profundo',d.deep)
      +row('s-light','leve',d.light)+row('s-rem','REM',d.rem)
      +row('s-awake','acordado',d.awake)+'</div><div class="sd-hr">FC noturna <b>'
      +(d.hravg||'—')+'</b> bpm <span class="sd-min">(mín '+(d.hrmin||'—')+')</span></div>';
  }
  document.querySelectorAll('.sleep-bar').forEach(function(el){
    el.addEventListener('mouseenter',function(){
      if(sdp) sdp.innerHTML=sleepPanel(el.dataset);
      document.querySelectorAll('.sleep-bar.on').forEach(function(x){x.classList.remove('on');});
      el.classList.add('on');
    });
  });

  // segmented toggles: button[data-toggle="groupId"][data-view="x"] shows [data-group][data-view]
  document.addEventListener('click',function(e){
    var b=e.target.closest('[data-toggle]'); if(!b)return;
    var g=b.getAttribute('data-toggle'), v=b.getAttribute('data-view');
    document.querySelectorAll('[data-toggle="'+g+'"]').forEach(function(x){
      x.classList.toggle('on', x===b);});
    document.querySelectorAll('[data-group="'+g+'"]').forEach(function(x){
      x.style.display = (x.getAttribute('data-view')===v)?'':'none';});
  });
})();
"""
