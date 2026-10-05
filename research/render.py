"""Render the analyst view: a self-contained HTML research desk with the board and every card
embedded as JSON. `page(..., standalone=True)` writes a full document (data/research/latest/index.html);
standalone=False returns the body-only form used when it's published as a claude.ai artifact."""
from __future__ import annotations

import json

HEAD = """<!doctype html><html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1,viewport-fit=cover"></head><body>"""


def _data(board: dict, cards: list[dict], manifest: dict) -> str:
    body = json.dumps({"board": board, "cards": cards, "manifest": manifest}, ensure_ascii=False,
                      separators=(",", ":"), default=str)
    return body.replace("</", "<\\/")  # keep </script> inside strings from closing the tag


def page(board: dict, cards: list[dict], manifest: dict, standalone: bool = True) -> str:
    html = (TEMPLATE.replace("/*__RX_CSS__*/", scope_css(RX_CSS, ".rx"))
            .replace("/*__RX_JS__*/", RX_JS)
            .replace("/*__DATA__*/null", _data(board, cards, manifest)))
    return (HEAD + html + "</body></html>") if standalone else html


def scope_css(css: str, scope: str) -> str:
    """Prefix every selector with `scope` (inside @media blocks too), so the research components can
    live inside another page (the Sharp Board) without leaking styles."""
    out, i, n = [], 0, len(css)
    def rule(sel_block: str) -> str:
        sels, _, body = sel_block.partition("{")
        pref = ",".join(f"{scope} {s.strip()}" for s in sels.split(",") if s.strip())
        return pref + "{" + body
    while i < n:
        j = css.find("{", i)
        if j < 0:
            out.append(css[i:]); break
        head = css[i:j]
        if head.strip().startswith("@media"):
            depth, k = 1, j + 1
            while depth and k < n:
                depth += {"{": 1, "}": -1}.get(css[k], 0); k += 1
            inner = css[j + 1:k - 1]
            out.append(head + "{" + scope_css(inner, scope) + "}")
            i = k
        elif head.strip().startswith("@keyframes"):
            depth, k = 1, j + 1
            while depth and k < n:
                depth += {"{": 1, "}": -1}.get(css[k], 0); k += 1
            out.append(css[i:k]); i = k
        else:
            k = css.find("}", j) + 1
            lead = head[:len(head) - len(head.lstrip())]
            out.append(lead + rule(css[i + len(lead):k]))
            i = k
    return "".join(out)


RX_CSS = r"""h2{font-size:1.6rem}
h3{font-size:1.05rem;text-transform:uppercase;letter-spacing:.06em}
p{margin:0}
.num{font-family:var(--f-mono);font-variant-numeric:tabular-nums;font-size:.86em}
.muted{color:var(--muted)} .small{font-size:.84rem}
.label{font:600 .68rem/1.2 var(--f-body);text-transform:uppercase;letter-spacing:.12em;color:var(--muted)}
.list{display:flex;flex-direction:column;gap:14px;position:sticky;top:calc(env(safe-area-inset-top,0px) + 110px);max-height:calc(100vh - 130px);overflow:auto;padding-right:4px}
@media (max-width:900px){.list{position:static;max-height:none}}
.layout{display:grid;grid-template-columns:330px minmax(0,1fr);gap:22px;align-items:start}
@media (max-width:900px){.layout{grid-template-columns:minmax(0,1fr)}}
.group{display:flex;flex-direction:column;gap:6px}
.game{all:unset;box-sizing:border-box;display:grid;grid-template-columns:auto minmax(0,1fr) auto;gap:4px 10px;align-items:start;background:var(--surface);border:1px solid var(--line);border-radius:5px;padding:9px 11px;cursor:pointer}
.game:hover{border-color:var(--accent)}
.game:focus-visible{outline:2px solid var(--accent);outline-offset:1px}
.game[aria-current="true"]{border-color:var(--accent);box-shadow:inset 3px 0 0 var(--accent)}
.game .mt{font-weight:600}
.game .why{grid-column:2/-1;font-size:.78rem;color:var(--muted);display:-webkit-box;-webkit-line-clamp:2;-webkit-box-orient:vertical;overflow:hidden}
.lg{font:700 .7rem/1 var(--f-display);letter-spacing:.06em;background:var(--ink);color:var(--bg);border-radius:3px;padding:4px 5px;margin-top:2px}
.pips{display:flex;gap:2px;margin-top:5px}
.pips i{width:6px;height:6px;border-radius:1px;background:var(--line)}
.pips i.on{background:var(--warn)} .pips i.hot{background:var(--bad)}
.chip{display:inline-block;font:600 .68rem/1 var(--f-body);text-transform:uppercase;letter-spacing:.07em;padding:4px 7px;border-radius:3px;white-space:nowrap;border:1px solid transparent}
.k-confirmed{background:var(--good-soft);color:var(--good)} .k-projected{background:var(--warn-soft);color:var(--warn)}
.k-reported{background:var(--accent-soft);color:var(--accent)} .k-derived{background:var(--derived-soft);color:var(--derived)}
.k-unknown{background:transparent;color:var(--muted);border-color:var(--line)}
.s-lean{background:var(--accent);color:var(--surface)} .s-watch{background:var(--warn-soft);color:var(--warn)}
.s-pass{background:var(--sunk);color:var(--muted);border-color:var(--line)} .s-insufficient{background:var(--bad-soft);color:var(--bad)}
.sev-alert{background:var(--bad-soft);color:var(--bad)} .sev-watch{background:var(--warn-soft);color:var(--warn)} .sev-info{background:var(--sunk);color:var(--muted);border-color:var(--line)}
.h-ok{background:var(--good-soft);color:var(--good)} .h-partial{background:var(--warn-soft);color:var(--warn)} .h-error{background:var(--bad-soft);color:var(--bad)} .h-skipped,.h-unknown{background:var(--sunk);color:var(--muted);border-color:var(--line)}
.dossier{display:flex;flex-direction:column;gap:16px;min-width:0}
.head{background:var(--surface);border:1px solid var(--line);border-radius:6px;padding:16px 18px;display:flex;flex-direction:column;gap:10px}
.head .meta{display:flex;flex-wrap:wrap;gap:6px 14px;color:var(--muted);font-size:.86rem}
.concl{display:grid;grid-template-columns:auto minmax(0,1fr);gap:4px 12px;align-items:start;border-top:1px dashed var(--line);padding-top:10px}
.sec{background:var(--surface);border:1px solid var(--line);border-radius:6px;padding:14px 16px;display:flex;flex-direction:column;gap:10px;min-width:0}
.sec > header{display:flex;justify-content:space-between;align-items:baseline;gap:10px;flex-wrap:wrap}
.q{font:600 .7rem/1.2 var(--f-body);letter-spacing:.12em;text-transform:uppercase;color:var(--accent)}
.scroll{overflow-x:auto}
table{border-collapse:collapse;width:100%;font-size:.86rem}
th,td{text-align:left;padding:7px 9px;border-bottom:1px solid var(--line);vertical-align:top}
th{font:600 .66rem/1.2 var(--f-body);text-transform:uppercase;letter-spacing:.1em;color:var(--muted);background:var(--sunk);white-space:nowrap}
td.n,th.n{text-align:right;white-space:nowrap}
tr:last-child td{border-bottom:0}
.sub{display:block;font-size:.76rem;color:var(--muted)}
.two{display:grid;grid-template-columns:repeat(auto-fit,minmax(260px,1fr));gap:14px}
ul.ev{margin:0;padding-left:18px;display:flex;flex-direction:column;gap:5px;font-size:.9rem}
.ev-sup li::marker{color:var(--good)} .ev-con li::marker{color:var(--bad)} .ev-unk li::marker{color:var(--warn)}
.tl{display:flex;flex-direction:column}
.tl .row{display:grid;grid-template-columns:7.5em 7em minmax(0,1fr);gap:4px 10px;padding:7px 0;border-bottom:1px solid var(--line);font-size:.86rem}
.tl .row:last-child{border-bottom:0}
@media (max-width:560px){.tl .row{grid-template-columns:minmax(0,1fr)}}
.chg{font-family:var(--f-mono);font-size:.8rem}
.cause{display:block;font-size:.78rem;color:var(--muted)}
details.group > summary{cursor:pointer;list-style:none}
details.group > summary::-webkit-details-marker{display:none}
details.group > summary h3::after{content:" ▸";color:var(--muted)}
details.group[open] > summary h3::after{content:" ▾"}
.contrib{display:flex;flex-direction:column;gap:5px}
.cb{display:grid;grid-template-columns:minmax(0,14em) minmax(0,1fr) 4.5em;gap:8px;align-items:center;font-size:.82rem}
.cb .track{height:10px;background:var(--sunk);border:1px solid var(--line);position:relative;border-radius:2px}
.cb .track span{position:absolute;top:0;bottom:0;background:var(--derived)}
.cb .track .zero{position:absolute;top:-2px;bottom:-2px;width:1px;background:var(--ink);left:50%}
@media (max-width:560px){.cb{grid-template-columns:minmax(0,1fr) 4.5em}.cb .track{grid-column:1/-1;grid-row:2}}
.spark svg{width:100%;height:70px;display:block}
.flags{display:flex;flex-direction:column;gap:6px}
.flag{display:grid;grid-template-columns:auto minmax(0,1fr);gap:2px 10px;font-size:.86rem}
.flag .why{grid-column:2;color:var(--muted);font-size:.8rem}
.empty{color:var(--muted);font-size:.86rem}
.note{font-size:.8rem;color:var(--muted);max-width:75ch}
.legend{display:flex;flex-wrap:wrap;gap:6px;align-items:center}
code{font-family:var(--f-mono);font-size:.78rem;background:var(--sunk);padding:1px 4px;border-radius:3px;overflow-wrap:anywhere}
.hist{border-left:3px solid var(--derived);padding-left:10px}
@media (prefers-reduced-motion:no-preference){.dossier{animation:fade .18s ease-out}@keyframes fade{from{opacity:.4}to{opacity:1}}}
"""

RX_JS = r"""const RX=(function(){
"use strict";
const esc=s=>String(s===undefined||s===null?'':s).replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
const am=o=>o===null||o===undefined?'—':(o>0?'+'+o:String(o));
const pct=(x,d=1)=>x===null||x===undefined?'—':(x*100).toFixed(d)+'%';
const sgn=(x,d=1)=>x===null||x===undefined?'—':(x>0?'+':'')+Number(x).toFixed(d);
const line=x=>x===null||x===undefined?'—':(x>0?'+':'')+x;
function tET(iso){ if(!iso) return '—'; const d=new Date(iso); if(isNaN(d)) return esc(iso); return d.toLocaleString('en-US',{timeZone:'America/New_York',weekday:'short',hour:'numeric',minute:'2-digit'})+' ET'; }
function ageMin(iso){ const d=new Date(iso); return isNaN(d)?null:Math.max(0,(Date.now()-d.getTime())/60000); }
function ageLbl(m){ if(m===null) return 'unknown age'; if(m<1) return 'just now'; if(m<60) return Math.round(m)+' min old'; if(m<2880) return (m/60).toFixed(m<600?1:0)+' h old'; return Math.round(m/1440)+' d old'; }
const kindChip=k=>`<span class="chip k-${esc(k||'unknown')}">${esc((k||'unknown'))}</span>`;
const statusCls=s=>s==='lean'?'s-lean':s==='watch'?'s-watch':s==='pass'?'s-pass':'s-insufficient';
const fact=f=>!f?'<span class="muted">—</span>':(f.value===null||f.value===undefined?`<span class="muted">unknown</span> ${kindChip('unknown')}<span class="sub">${esc(f.reason||'')}</span>`:`${esc(typeof f.value==='object'?JSON.stringify(f.value):f.value)} ${kindChip(f.kind)}${f.source?`<span class="sub">${esc(f.source)}${f.note?' · '+esc(f.note):''}</span>`:''}`);

/* ---------- board ---------- */
function pips(score){ const n=Math.min(8,Math.round(score)); return `<span class="pips" aria-label="importance ${score}">${Array.from({length:8},(_,i)=>`<i class="${i<n?(n>=6?'hot':'on'):''}"></i>`).join('')}</span>`; }
function item(e){ return `<button class="game" data-key="${esc(e.key)}" aria-current="false"><span class="lg">${esc(e.league)}</span>
  <span><span class="mt">${esc(e.matchup)}</span><span class="sub">${esc(e.start||'')}</span>${pips(e.importance)}</span>
  <span class="chip ${statusCls(e.research_status)}">${esc(e.research_status==='insufficient information'?'need info':e.research_status||'—')}</span>
  ${e.reasons&&e.reasons.length?`<span class="why">${esc(e.reasons[0])}</span>`:(e.why_stable?`<span class="why">${esc(e.why_stable)}</span>`:'')}</button>`; }
function group(title,note,rows){ return `<section class="group"><div><h3>${esc(title)} <span class="muted num">${rows.length}</span></h3>${note?`<p class="note">${esc(note)}</p>`:''}</div>${rows.length?rows.map(item).join(''):'<p class="empty">None.</p>'}</section>`; }
/* ---------- dossier ---------- */
function spark(series){
  const pts=(series||[]).filter(p=>p.spread_home!==null&&p.spread_home!==undefined);
  if(pts.length<2) return '<p class="empty">Not enough observations for a line chart yet.</p>';
  const W=600,H=70,L=34,R=8,T=8,Bm=16; const ys=pts.map(p=>p.spread_home); let lo=Math.min(...ys),hi=Math.max(...ys); if(hi-lo<1){hi+=0.5;lo-=0.5;}
  const x=i=>L+(W-L-R)*i/(pts.length-1), y=v=>T+(H-T-Bm)*(v-lo)/(hi-lo);
  const d=pts.map((p,i)=>(i?'L':'M')+x(i).toFixed(1)+' '+y(p.spread_home).toFixed(1)).join(' ');
  return `<div class="spark"><svg viewBox="0 0 ${W} ${H}" role="img" aria-label="Home spread from ${line(ys[0])} to ${line(ys[ys.length-1])}">
    <text x="${L-6}" y="${(y(hi)+4).toFixed(1)}" text-anchor="end" font-size="10" fill="var(--muted)" font-family="var(--f-mono)">${line(hi)}</text>
    <text x="${L-6}" y="${(y(lo)+4).toFixed(1)}" text-anchor="end" font-size="10" fill="var(--muted)" font-family="var(--f-mono)">${line(lo)}</text>
    <path d="${d}" fill="none" stroke="var(--accent)" stroke-width="2"/>
    ${pts.map((p,i)=>`<circle cx="${x(i).toFixed(1)}" cy="${y(p.spread_home).toFixed(1)}" r="${i===pts.length-1?4:2.5}" fill="${i===pts.length-1?'var(--accent)':'var(--surface)'}" stroke="var(--accent)" stroke-width="1.5"/>`).join('')}
    <text x="${L}" y="${H-3}" font-size="10" fill="var(--muted)" font-family="var(--f-mono)">first seen</text><text x="${W-R}" y="${H-3}" text-anchor="end" font-size="10" fill="var(--muted)" font-family="var(--f-mono)">latest</text>
  </svg></div>`;
}
function px(side){ if(!side) return '—'; return `<span class="num">${am(side.fanduel)}</span><span class="sub">DK ${am(side.draftkings)} · best ${am(side.best)}${side.best_book?' '+esc(side.best_book):''}</span>`; }
function mrow(label,pt,kind,c){
  if(!pt) return `<tr><td>${label}</td><td colspan="4" class="muted">—</td></tr>`;
  const ts=`<span class="sub">${tET(pt.t)}${pt.last_seen&&pt.last_seen!==pt.t?' → '+tET(pt.last_seen):''}</span>`;
  if(kind==='spread') return `<tr><td>${label}${ts}</td><td class="n num">${esc(c.game.home.abbr)} ${line(pt.home_line)}</td><td class="n">${px(pt.away)}</td><td class="n">${px(pt.home)}</td><td class="n num">${pct(pt.fair_home)}</td></tr>`;
  if(kind==='total') return `<tr><td>${label}${ts}</td><td class="n num">${pt.line}</td><td class="n">${px(pt.over)}</td><td class="n">${px(pt.under)}</td><td class="n num">${pct(pt.fair_over)}</td></tr>`;
  return `<tr><td>${label}${ts}</td><td class="n num">—</td><td class="n">${px(pt.away)}</td><td class="n">${px(pt.home)}</td><td class="n num">${pct(pt.fair_home)}</td></tr>`;
}
function marketSec(c){
  const m=c.market||{}; const blocks=['spread','moneyline','total'].map(k=>{ const x=m[k]||{};
    if(!x.available) return `<div><span class="label">${k}</span><p class="empty">${esc(x.reason||'no data')}</p></div>`;
    const cols=k==='total'?['Line','Over (FanDuel)','Under (FanDuel)','No-vig over']:k==='spread'?['Line','Away (FanDuel)','Home (FanDuel)','No-vig home cover']:['','Away (FanDuel)','Home (FanDuel)','No-vig home win'];
    const mv=x.movement||{}; const mvs=[];
    if(mv.home_line) mvs.push(`line ${sgn(mv.home_line,1)} toward ${esc(mv.toward==='home'?c.game.home.abbr:c.game.away.abbr)}`);
    if(mv.line) mvs.push(`total ${sgn(mv.line,1)} (${esc(mv.toward)})`);
    if(mv.fair_home) mvs.push(`no-vig home ${sgn(mv.fair_home*100,1)} pp`);
    if(mv.fair_over) mvs.push(`no-vig over ${sgn(mv.fair_over*100,1)} pp`);
    Object.entries(mv).filter(([kk])=>kk.startsWith('fanduel_')).forEach(([kk,v])=>mvs.push(`FanDuel ${esc(kk.slice(8))} ${am(v.from)} → ${am(v.to)} (${sgn(v.implied_change*100,1)} pp implied)`));
    return `<div class="scroll"><table><thead><tr><th>${esc(k)} · ${x.observations} obs</th>${cols.map(h=>`<th class="n">${h}</th>`).join('')}</tr></thead><tbody>
      ${mrow('Open (first seen)',x.open,k,c)}${mrow('Current',x.current,k,c)}${x.close?mrow('Close (last before start)',x.close,k,c):`<tr><td colspan="5" class="muted small">Close: ${esc(x.close_note)}</td></tr>`}</tbody></table>
      <p class="small">${mvs.length?'<strong>Movement:</strong> '+mvs.join(' · '):'<span class="muted">No movement since first seen.</span>'}</p></div>`; }).join('');
  return `<section class="sec"><header><div><span class="q">What does the market say · how did it move</span><h2>Market</h2></div>
    <span class="legend">${kindChip('reported')}<span class="small muted">FanDuel = actionable · DK/best = context · no-vig = estimate, not a price</span></span></header>
    ${spark(m.series)}${blocks}${(m.event_flags||[]).length?`<p class="note">Feed flags: ${m.event_flags.map(esc).join('; ')}</p>`:''}</section>`;
}
function modelSec(c){
  const m=c.model||{}, cmp=c.comparison||{};
  if(!m.available) return `<section class="sec"><header><div><span class="q">What does Boolin think</span><h2>Boolin view</h2></div>${kindChip('unknown')}</header><p class="empty">${esc(m.reason||'No projection.')}</p></section>`;
  const maxAbs=Math.max(0.01,...(m.contributions||[]).map(x=>Math.abs(x.margin)));
  const bars=(m.contributions||[]).map(x=>{ const w=Math.abs(x.margin)/maxAbs*50; const left=x.margin>=0?50:50-w;
    return `<div class="cb"><span>${esc(x.name)}<span class="sub">${esc(x.note||'')}</span></span><span class="track"><span style="left:${left}%;width:${w}%"></span><span class="zero"></span></span><span class="num" style="text-align:right">${sgn(x.margin,2)}</span></div>`; }).join('');
  const rows=(cmp.rows||[]).map(r=>`<tr><td>${esc(r.dimension)}${r.note?`<span class="sub">${esc(r.note)}</span>`:''}</td><td class="n num">${r.unit==='pp'?pct(r.market):r.dimension==='Total'?r.market:line(r.market)}</td><td class="n num">${r.unit==='pp'?pct(r.boolin):r.dimension==='Total'?r.boolin:line(r.boolin)}</td><td class="n num">${sgn(r.difference,1)} ${esc(r.unit)}</td><td><span class="chip ${r.label==='aligned'?'s-pass':r.label==='mild disagreement'?'sev-info':r.label==='significant disagreement'?'sev-watch':'sev-alert'}">${esc(r.label)}</span><span class="sub">${esc(r.direction)}</span></td></tr>`).join('');
  return `<section class="sec"><header><div><span class="q">What does Boolin think · where does it disagree</span><h2>Boolin vs market</h2></div>
    <span class="legend">${kindChip('derived')}<span class="small muted">${esc(m.version)} · confidence ${esc(m.confidence)}</span></span></header>
    <div class="two"><div><span class="label">Projection</span><p class="num" style="font-size:1rem">${esc(c.game.home.abbr)} ${pct(m.home_win_p)} · line ${esc(c.game.home.abbr)} ${line(Math.round(-m.proj_margin_home*10)/10)} · total ${m.proj_total??'—'}</p></div>
      <div><span class="label">Overall</span><p>${esc(cmp.overall||'—')}</p>${cmp.caveat?`<p class="note">${esc(cmp.caveat)}</p>`:''}</div></div>
    ${rows?`<div class="scroll"><table><thead><tr><th>Measure</th><th class="n">Market</th><th class="n">Boolin</th><th class="n">Gap</th><th>Label</th></tr></thead><tbody>${rows}</tbody></table></div>`:''}
    <div><span class="label">Why Boolin has this number (home-margin units, sums to the projection)</span><div class="contrib" style="margin-top:6px">${bars}</div></div>
    ${(m.sample_warnings||[]).length?`<ul class="ev ev-unk">${m.sample_warnings.map(w=>`<li>${esc(w)}</li>`).join('')}</ul>`:''}
    <p class="note">Assumptions: ${(m.assumptions||[]).map(esc).join(' ')} ${esc(cmp.reminder||'')}</p></section>`;
}
function changesSec(c){
  const ev=(c.recent_changes||[]).slice().reverse();
  return `<section class="sec"><header><div><span class="q">What changed</span><h2>Timeline · last 24 h</h2></div><span class="small muted">${c.change_count_total||0} change(s) on record for this game</span></header>
    ${ev.length?`<div class="tl">${ev.map(e=>`<div class="row"><span class="num muted">${tET(e.t)}</span><span class="chip ${e.type==='model'?'k-derived':e.type==='injury_status'||e.type==='starter'?'sev-watch':'k-reported'}">${esc(e.type.replace('_',' '))}</span>
      <span><strong>${esc(e.field)}</strong> <span class="chg">${esc(e.old)} → ${esc(e.new)}</span><span class="cause">${esc(e.cause)} · ${esc(e.source)}</span></span></div>`).join('')}</div>`:'<p class="empty">No changes recorded in the last 24 hours.</p>'}</section>`;
}
function evidenceSec(c){
  const s=c.summary||{}; const L=(arr,cls)=>arr&&arr.length?`<ul class="ev ${cls}">${arr.map(x=>`<li>${esc(x)}</li>`).join('')}</ul>`:'<p class="empty">None in the data.</p>';
  return `<section class="sec"><header><div><span class="q">What supports it · what argues against it · what's unknown</span><h2>Evidence</h2></div>
    <span class="small muted">Judged relative to: ${esc(s.thesis_team||'—')} (${esc(s.thesis_basis||'')})</span></header>
    <div><span class="label">Why this game matters</span>${L(s.why_it_matters,'')}</div>
    <div class="two"><div><span class="label">Supporting</span>${L(s.supporting,'ev-sup')}</div><div><span class="label">Contradicting</span>${L(s.contradicting,'ev-con')}</div></div>
    <div><span class="label">Unknowns / unresolved</span>${L(s.unknowns,'ev-unk')}</div></section>`;
}
function availSec(c){
  const a=c.availability||{}; const st=a.starters||{}; const lu=a.lineups||{};
  const inj=(a.injuries||[]);
  return `<section class="sec"><header><div><span class="q">Who is playing</span><h2>Availability</h2></div><span class="legend">${['confirmed','reported','projected','unknown'].map(kindChip).join('')}</span></header>
    <div class="two">${['away','home'].map(s=>`<div><span class="label">${esc(c.game[s].abbr)} starter</span><p>${fact(st[s])}</p>${lu[s]?`<span class="label">Lineup</span><p>${lu[s].value?`posted ${kindChip(lu[s].kind)}`:fact(lu[s])}</p>`:''}</div>`).join('')}</div>
    ${inj.length?`<div class="scroll"><table><thead><tr><th>Player</th><th>Team</th><th>Status</th><th>Detail</th><th>Source</th></tr></thead><tbody>${inj.map(r=>`<tr><td>${esc(r.player)}${r.key_player?' <span class="chip sev-watch">key</span>':''}<span class="sub">${esc(r.pos||'')}</span></td><td>${esc(r.team)}</td><td>${esc(r.status)}${r.practice?`<span class="sub">${esc(r.practice)}</span>`:''}</td><td class="small">${esc(r.detail||'')}</td><td class="small muted">${kindChip(r.kind)}<span class="sub">${esc(r.source)}</span></td></tr>`).join('')}</tbody></table></div>`:'<p class="empty">No injuries listed in the data for this game.</p>'}
    ${a.injury_report&&a.injury_report.week?`<p class="note">NFL statuses are the week ${esc(a.injury_report.week)} report; game-day inactives come ~90 min before kickoff.</p>`:''}</section>`;
}
function envSec(c){
  const e=c.environment||{}; const rows=Object.entries(e).filter(([k,v])=>v&&typeof v==='object'&&'kind' in v);
  return `<section class="sec"><header><div><span class="q">Conditions</span><h2>Environment</h2></div><span class="small muted">${e.outdoors===true?'outdoors':e.outdoors===false?'indoors':'roof unknown'}${e.venue?' · '+esc(e.venue):c.game.venue?' · '+esc(c.game.venue):''}</span></header>
    ${rows.length?`<div class="two">${rows.map(([k,v])=>`<div><span class="label">${esc(k.replace('_mph',' (mph)').replace('_f',' (°F)').replace('_',' '))}</span><p>${fact(v)}</p></div>`).join('')}</div>`:'<p class="empty">No environment data for this league.</p>'}</section>`;
}
function freshSec(c){
  const f=c.freshness||{};
  return `<section class="sec"><header><div><span class="q">How fresh is it</span><h2>Data freshness</h2></div></header><div class="scroll"><table><thead><tr><th>Component</th><th>Age</th><th>Kind</th><th>Status</th><th>Source</th></tr></thead><tbody>
    ${Object.entries(f).map(([k,v])=>{ const live=v.as_of?ageLbl(ageMin(v.as_of)):'unknown age'; return `<tr><td>${esc(k.replace('_',' '))}</td><td class="num">${esc(live)}<span class="sub">${tET(v.as_of)}</span></td><td>${kindChip(v.kind)}</td><td><span class="chip h-${esc(v.status)}">${esc(v.status)}</span></td><td class="small">${esc(v.source)}</td></tr>`; }).join('')}
    </tbody></table></div><p class="note">Ages update live from the stored timestamps. Projected and reported items are never shown as confirmed.</p></section>`;
}
function flagsSec(c){
  const f=c.flags||[];
  return `<section class="sec"><header><div><span class="q">What needs a look</span><h2>Research flags</h2></div><span class="small muted">${f.length} flag(s)</span></header>
    ${f.length?`<div class="flags">${f.map(x=>`<div class="flag"><span class="chip sev-${esc(x.severity)}">${esc(x.severity)}</span><strong>${esc(x.title)}</strong><span class="why">${esc(x.why)}</span></div>`).join('')}</div>`:'<p class="empty">No flags.</p>'}</section>`;
}
function scenSec(c){
  const s=c.scenarios||{}; const rows=s.scenarios||[];
  return `<section class="sec"><header><div><span class="q">What if</span><h2>Scenarios</h2></div>${kindChip('derived')}</header>
    ${s.baseline?`<p class="small">Baseline: ${esc(c.game.home.abbr)} ${pct(s.baseline.home_win_p)} · margin ${sgn(s.baseline.proj_margin_home,2)} · total ${s.baseline.proj_total??'—'}</p>`:''}
    ${rows.length?`<div class="scroll"><table><thead><tr><th>Scenario</th><th class="n">${esc(c.game.home.abbr)} win</th><th class="n">Δ</th><th class="n">Margin</th><th class="n">Total</th></tr></thead><tbody>
    ${rows.map(r=>r.supported?`<tr><td>${esc(r.name)}${r.note?`<span class="sub">${esc(r.note)}</span>`:''}</td><td class="n num">${pct(r.home_win_p)}</td><td class="n num">${sgn(r.delta.home_win_p*100,1)} pp</td><td class="n num">${sgn(r.proj_margin_home,2)} <span class="sub">${sgn(r.delta.proj_margin_home,2)}</span></td><td class="n num">${r.proj_total??'—'} <span class="sub">${r.delta.proj_total===null?'':sgn(r.delta.proj_total,2)}</span></td></tr>`:`<tr><td>${esc(r.name)}</td><td colspan="4" class="muted small">Not modeled: ${esc(r.reason)}</td></tr>`).join('')}</tbody></table></div>`:`<p class="empty">${esc(s.note||'No scenarios.')}</p>`}
    <p class="note">${esc(s.note||'')}</p></section>`;
}
function compSec(c){
  const h=c.comparables||{};
  const rows=(h.situations||[]);
  return `<section class="sec hist"><header><div><span class="q">${esc(h.label||'HISTORICAL CONTEXT')}</span><h2>Comparable situations</h2></div><span class="small muted">${esc(h.source||'')}</span></header>
    ${rows.length?`<div class="scroll"><table><thead><tr><th>Situation</th><th class="n">Games</th><th class="n">Home cover / win</th><th class="n">Margin vs spread</th><th class="n">Over %</th></tr></thead><tbody>
    ${rows.map(r=>`<tr><td>${esc(r.situation)}${r.seasons?`<span class="sub">${esc(r.seasons)}</span>`:''}${r.warning?`<span class="sub" style="color:var(--warn)">${esc(r.warning)}</span>`:''}</td><td class="n num">${r.n}</td><td class="n num">${r.home_cover_pct!==undefined?pct(r.home_cover_pct,0):pct(r.home_win_pct,0)}</td><td class="n num">${r.avg_home_margin_vs_spread!==undefined?sgn(r.avg_home_margin_vs_spread,1):'—'}</td><td class="n num">${r.over_pct!==undefined?pct(r.over_pct,0):'—'}</td></tr>`).join('')}</tbody></table></div>`:`<p class="empty">${esc(h.reason||'No comparable situations computed.')}</p>`}
    ${(h.unsupported||[]).map(u=>`<p class="note">${esc(u)}</p>`).join('')}<p class="note">${esc(h.caution||'Historical context only; not evidence this game will go the same way.')}</p></section>`;
}
function notebookSec(c){
  const n=c.notebook; const pg=c.postgame;
  const g=pg&&pg.grade; const comps=g?Object.entries(g.components):[];
  return `<section class="sec"><header><div><span class="q">Analyst notebook · postgame</span><h2>Notebook</h2></div></header>
    ${n?`<div class="two"><div><span class="label">Thesis</span><p>${esc((n.thesis||{}).statement||'—')}</p><p class="small muted">${esc((n.thesis||{}).market||'')} ${esc((n.thesis||{}).side||'')}</p></div>
      <div><span class="label">Decision</span><p>${esc(n.decision||'—')}</p>${n.entry_trigger?`<p class="small">Entry trigger: ${esc(n.entry_trigger)}</p>`:''}${n.position?`<p class="small num">Position: ${esc(n.position.market)} ${esc(n.position.side)} ${n.position.line??''} ${am(n.position.price)} (${esc(n.position.book||'')})</p>`:''}</div></div>
      ${n.postgame_review?`<div><span class="label">Postgame review</span><p>${esc(n.postgame_review.text||'')}</p><p class="small muted">Thesis correct: ${esc(n.postgame_review.thesis_correct||'—')}</p></div>`:''}
      <p class="note">Updated ${tET(n.updated_at)} · ${n.history?n.history.length:0} edit(s).</p>`
    :`<p class="empty">No notebook for this game yet.</p><p class="note">Add one: <code>python -m research.notebook set ${esc(c.key)} --thesis "..." --side home --market spread --decision monitor --trigger "..."</code></p>`}
    ${g?`<div><span class="label">Postgame grade</span><p class="num" style="font-size:1rem">Final ${esc(c.game.away.abbr)} ${pg.result.away_score} – ${esc(c.game.home.abbr)} ${pg.result.home_score} · overall ${esc(g.overall.grade||'not gradable')}</p>
      <div class="scroll"><table><thead><tr><th>Component</th><th>Grade</th><th>Detail</th></tr></thead><tbody>${comps.map(([k,v])=>`<tr><td>${esc(k.replace('_',' '))}</td><td class="num">${v.gradable?esc(v.grade):'—'}</td><td class="small">${v.gradable?esc(Object.entries(v).filter(([kk])=>!['gradable','score','grade'].includes(kk)).map(([kk,vv])=>kk+': '+(typeof vv==='object'?JSON.stringify(vv):vv)).join(' · ')):esc(v.reason)}</td></tr>`).join('')}</tbody></table></div><p class="note">${esc(g.note)}</p></div>`:''}</section>`;
}
function dossier(c){
  if(!c) return '<section class="sec"><p class="empty">Pick a game.</p></section>';
  const s=(c.summary||{}).conclusion||{};
  return `<header class="head"><div><span class="label">${esc(c.league)} · ${esc(c.status)}${c.game.context&&c.game.context.series?' · '+esc(c.game.context.series):''}</span><h2>${esc(c.game.away.name)} @ ${esc(c.game.home.name)}</h2></div>
    <div class="meta"><span>${esc(c.start_label)}</span>${c.game.venue?`<span>${esc(c.game.venue)}</span>`:''}<span>TV: ${c.game.tv&&c.game.tv.value?esc(c.game.tv.value):'unknown'}</span><span>built ${tET(c.built_at)}</span></div>
    <div class="concl"><span class="chip ${statusCls(s.status)}">${esc(s.status||'—')}</span><div><p>${esc(s.detail||'')}</p>
      ${s.analyst_decision?`<p class="small">Analyst decision: <strong>${esc(s.analyst_decision)}</strong></p>`:''}
      ${s.trigger?`<p class="small muted">Trigger: ${esc(s.trigger)}</p>`:''}
      ${(s.would_change_if||[]).length?`<p class="small muted">Would change if: ${s.would_change_if.map(esc).join(' · ')}</p>`:''}</div></div></header>
    ${marketSec(c)}${modelSec(c)}${changesSec(c)}${evidenceSec(c)}${availSec(c)}${flagsSec(c)}${envSec(c)}${freshSec(c)}${scenSec(c)}${compSec(c)}${notebookSec(c)}`;
}
return {esc,item,group,dossier,tET,ageMin,ageLbl,statusCls};
})();"""

TEMPLATE = r"""<title>Boolin Research Desk</title>
<link rel="preconnect" href="https://fonts.googleapis.com">
<link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
<link rel="stylesheet" href="https://fonts.googleapis.com/css2?family=Saira+Condensed:wght@600;700&family=Public+Sans:wght@400;500;600&family=JetBrains+Mono:wght@500&display=swap">
<style>

/* Layout: sticky slate bar; left = research board ranked by importance; right = the selected game's dossier, read top to bottom in the analyst's question order */
:root{
  --bg:#F1F3EF; --surface:#FFFFFF; --sunk:#F7F8F5; --ink:#17201C; --muted:#5A6660; --line:#D6DBD4;
  --accent:#0E6B66; --accent-soft:#DDEFEC;
  --good:#2D7A3A; --good-soft:#E1F1E3; --warn:#9A5B00; --warn-soft:#F7EAD2; --bad:#B03A2E; --bad-soft:#F7E1DE;
  --derived:#5A49B0; --derived-soft:#E9E6F7;
  --f-display:"Saira Condensed","Arial Narrow",Arial,sans-serif;
  --f-body:"Public Sans",system-ui,-apple-system,"Segoe UI",sans-serif;
  --f-mono:"JetBrains Mono",ui-monospace,Menlo,Consolas,monospace;
}
@media (prefers-color-scheme: dark){:root:not([data-theme="light"]){
  color-scheme:dark; --bg:#0D1311; --surface:#141C19; --sunk:#18211E; --ink:#E2E9E5; --muted:#93A29B; --line:#26322D;
  --accent:#5CC4BB; --accent-soft:#16302D; --good:#6CC67A; --good-soft:#15291A; --warn:#E6B05C; --warn-soft:#332611;
  --bad:#EF8C80; --bad-soft:#3A1C18; --derived:#A99CF0; --derived-soft:#231F3D;}}
:root[data-theme="dark"]{
  color-scheme:dark; --bg:#0D1311; --surface:#141C19; --sunk:#18211E; --ink:#E2E9E5; --muted:#93A29B; --line:#26322D;
  --accent:#5CC4BB; --accent-soft:#16302D; --good:#6CC67A; --good-soft:#15291A; --warn:#E6B05C; --warn-soft:#332611;
  --bad:#EF8C80; --bad-soft:#3A1C18; --derived:#A99CF0; --derived-soft:#231F3D;}
*{box-sizing:border-box}
html,body{background:var(--bg)}
body{margin:0;color:var(--ink);font:15px/1.5 var(--f-body)}
h1,h2,h3{font-family:var(--f-display);margin:0;line-height:1.05;letter-spacing:.01em;text-wrap:balance}
h1{font-size:1.9rem;text-transform:uppercase}
h2{font-size:1.6rem}
h3{font-size:1.05rem;text-transform:uppercase;letter-spacing:.06em}
p{margin:0}
.num{font-family:var(--f-mono);font-variant-numeric:tabular-nums;font-size:.86em}
.muted{color:var(--muted)} .small{font-size:.84rem}
.label{font:600 .68rem/1.2 var(--f-body);text-transform:uppercase;letter-spacing:.12em;color:var(--muted)}
.bar{position:sticky;top:env(safe-area-inset-top,0px);z-index:4;background:var(--bg);border-bottom:2px solid var(--ink)}
.bar-in{max-width:1280px;margin:0 auto;padding:12px 16px;display:flex;flex-wrap:wrap;gap:8px 24px;align-items:flex-end;justify-content:space-between}
.health{display:flex;flex-wrap:wrap;gap:6px}
.wrap{max-width:1280px;margin:0 auto;padding-inline:16px;padding-block:18px 64px;display:grid;grid-template-columns:330px minmax(0,1fr);gap:22px;align-items:start}
@media (max-width:900px){.wrap{grid-template-columns:minmax(0,1fr)}}
@media (max-width:600px){.bar{position:static}}
/*__RX_CSS__*/
</style>
<header class="bar rx"><div class="bar-in">
  <div><span class="label">Research desk · evidence, uncertainty, market context</span><h1>Boolin Research Desk</h1>
    <span class="small muted" id="slate"></span></div>
  <div class="health" id="health" aria-label="Source health"></div>
</div></header>
<main class="wrap rx">
  <nav class="list" id="list" aria-label="Research board"></nav>
  <article class="dossier" id="dossier" aria-live="polite"></article>
</main>
<script>
/*__RX_JS__*/
(function(){
"use strict";
const D=/*__DATA__*/null;
const cards=Object.fromEntries((D.cards||[]).map(c=>[c.key,c]));
const {esc,item,group,tET,ageMin,ageLbl}=RX;
/* ---------- top bar ---------- */
const M=D.manifest||{}; const B=D.board||{};
document.getElementById('slate').textContent=`Slate ${B.slate_date||'—'} · built ${tET(M.built_at)} (${ageLbl(ageMin(M.built_at))}) · ${M.cards||0} cards · research ${M.status||'?'}`;
document.getElementById('health').innerHTML=Object.entries(M.inputs||{}).filter(([k,v])=>v.status).map(([k,v])=>`<span class="chip h-${esc(v.status)}" title="${esc(k)} pulled ${esc(v.pulled_at_et||'')}">${esc(k)} ${esc(v.status)}</span>`).join('');

document.getElementById('list').innerHTML=
  group('Needs attention','Ranked by what changed, what is unresolved and where Boolin splits from the market. Not a bet ranking.',B.attention||[])+
  group('Stable','No meaningful change, no strong split, inputs fresh.',B.stable||[])+
  `<details class="group"><summary><h3>Upcoming <span class="muted num">${(B.upcoming||[]).length}</span></h3><p class="note">Later games already tracked for line history.</p></summary>${(B.upcoming||[]).slice(0,25).map(item).join('')}</details>`;

function show(key){
  const c=cards[key]; document.querySelectorAll('.game').forEach(b=>b.setAttribute('aria-current',String(b.dataset.key===key)));
  document.getElementById('dossier').innerHTML=RX.dossier(c);
}
document.getElementById('list').addEventListener('click',e=>{ const b=e.target.closest('.game'); if(!b) return; show(b.dataset.key); try{ history.replaceState(null,'','#'+b.dataset.key); }catch(_){}
  if(window.matchMedia('(max-width:900px)').matches) document.getElementById('dossier').scrollIntoView({behavior:'smooth',block:'start'}); });
const first=(location.hash&&cards[location.hash.slice(1)])?location.hash.slice(1):((B.attention||[])[0]||(B.stable||[])[0]||(B.upcoming||[])[0]||{}).key;
show(first);
})();
</script>"""


def main(argv=None) -> int:
    """python -m research.render [--artifact OUT]  — rebuild the HTML view from data/research/latest/*.json.
    index.html isn't committed (it duplicates cards.json), so this regenerates it on demand."""
    import argparse
    from pathlib import Path
    from .build import RESEARCH
    ap = argparse.ArgumentParser(prog="research.render")
    ap.add_argument("--root", default=str(RESEARCH))
    ap.add_argument("--artifact", help="also write a body-only copy (for publishing as a claude.ai artifact)")
    a = ap.parse_args(argv)
    latest = Path(a.root) / "latest"
    board = json.loads((latest / "board.json").read_text())
    cards = json.loads((latest / "cards.json").read_text())["cards"]
    manifest = json.loads((latest / "manifest.json").read_text())
    (latest / "index.html").write_text(page(board, cards, manifest))
    if a.artifact:
        Path(a.artifact).write_text(page(board, cards, manifest, standalone=False))
    print(latest / "index.html")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
