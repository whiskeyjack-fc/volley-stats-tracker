// Shared chart constants — report.html, season_report.html, player_report.html
const CAT_COLORS = {
  serve:    { base:"59,130,246",  results:{ error:"232,81,74", "1-serve":"249,115,22", "2-serve":"245,200,66", "3-serve":"86,190,120", ace:"16,185,129" } },
  attack:   { base:"249,115,22",  results:{ kill:"62,207,111", error:"232,81,74" } },
  receive:  { base:"16,185,129",  results:{ error:"232,81,74", "1-receive":"249,115,22", "2-receive":"245,200,66", "3-receive":"86,190,120", overpass:"180,100,240" } },
  block:    { base:"139,92,246",  results:{ kill:"62,207,111", error:"232,81,74" } },
  freeball: { base:"6,182,212",   results:{ error:"232,81,74", "1-freeball":"249,115,22", "2-freeball":"245,200,66", "3-freeball":"86,190,120" } },
  fault:    { base:"244,63,94",   results:{ fault:"244,63,94" } },
};

const RESULT_LABELS = {
  error:"Error","1-serve":"S1","2-serve":"S2","3-serve":"S3",ace:"Ace",
  kill:"Kill","1-receive":"R1","2-receive":"R2","3-receive":"R3",overpass:"OvP",
  "1-freeball":"F1","2-freeball":"F2","3-freeball":"F3",fault:"Fault"
};

// ── Shared axis / plugin defaults ─────────────────────────────────────
const xAxis  = { ticks:{color:"#8891b2",font:{size:10}}, grid:{color:"rgba(255,255,255,.05)"} };
const yAxis  = { ticks:{color:"#8891b2",font:{size:10}}, grid:{color:"rgba(255,255,255,.07)"} };
const legend = { labels:{color:"#8891b2",font:{size:10}} };
const base   = { responsive:true, maintainAspectRatio:false, plugins:{ legend }, scales:{ x:xAxis, y:yAxis } };
