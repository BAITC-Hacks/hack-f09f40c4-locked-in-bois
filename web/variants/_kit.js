/* Shared data + helpers for the 8 design boards in web/variants/.
   Every number comes from real engine responses recorded in web/mock/*.json (doc example plan, smog crisis). */
(function () {
  'use strict';
  const r2 = (x) => Math.round(x * 100) / 100;
  const num = (x, d = 2) => (x == null || isNaN(x) ? '—' : Number(x).toFixed(d).replace('.', ','));
  const int = (x) => (x == null || isNaN(x) ? '—' : Math.round(Number(x)).toLocaleString('ru-RU'));
  const sign = (x, d = 2) => (x == null || isNaN(x) ? '—' : (x > 0 ? '+' : x < 0 ? '−' : '±') + num(Math.abs(x), d));
  const esc = (s) => String(s ?? '').replace(/[&<>"']/g, (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));
  const unq = (s) => String(s || '').replace(/^«|»$/g, '');
  const SHORT = { T1: 'дороги', T2: 'общ. транспорт', E1: 'зелень', E2: 'воздух', S1: 'школы', S2: 'поликлиники', B1: 'улицы', B2: 'ДТП', C1: 'ЖКХ', C2: 'обращения' };
  const QL = ['старт', 'I кв. 27', 'II кв. 27', 'III кв. 27', 'IV кв. 27', 'I кв. 28', 'II кв. 28', 'III кв. 28', 'IV кв. 28'];

  async function load(base = '../mock/') {
    const get = (n) => fetch(base + n + '.json').then((r) => { if (!r.ok) throw new Error(n); return r.json(); });
    const [ds, score, opt, ai, nar, shock, res, narC] = await Promise.all(
      ['dataset', 'score', 'optimize', 'analyze', 'narrative', 'shock', 'resolve', 'narrative_crisis'].map(get));
    const M = Object.fromEntries(ds.measures.map((m) => [m.id, m]));
    const DIR = Object.fromEntries(ds.directions.map((d) => [d.id, d]));
    const plan = ds.reference.doc_example.decisions;
    const districts = ds.districts.map((d) => {
      const s = score.districts[d.name], a = score.approval.districts[d.name];
      return { ...d, before: s.before, after: s.after, Db: s.D_before, Da: s.D_after, dD: r2(s.D_after - s.D_before), approval: a.approval, got: a.got_district_measure };
    });
    const D = {
      ds, score, opt, ai, nar, shock, res, narC, M, DIR, plan,
      decs: plan.map((p) => ({ ...p, m: M[p.measure] })),
      measures: ds.measures, directions: ds.directions, inds: ds.indicators, districts,
      base: score.baseline, sc: score.score, delta: score.delta, cost: score.cost,
      appr: score.approval.city, thr: score.approval.threshold, reelected: score.approval.reelected,
      rank: opt.rank, total: opt.total_valid, top: (opt.rank / opt.total_valid) * 100, gap: opt.gap_to_best,
      best: opt.best, balanced: opt.balanced, pareto: opt.pareto,
      timeline: score.timeline, contrib: score.contributions.slice().sort((a, b) => b.marginal - a.marginal),
      council: narC.council, paper: narC.newspaper, councilPre: nar.council,
      event: shock.event,
      board: [
        { team: 'Машинный оптимум', score: opt.best.score, approval: opt.best.approval, cost: opt.best.cost, pin: 'opt' },
        { team: 'Баланс ИИ', score: opt.balanced.score, approval: opt.balanced.approval, cost: opt.balanced.cost, pin: 'bal' },
        { team: 'Пример из ТЗ', score: score.score, approval: score.approval.city, cost: score.cost, pin: 'me' },
        { team: 'Команда «Есиль-2028»', score: 56.21, approval: 44.9, cost: 99, demo: true },
        { team: 'Команда «Сарыарка вперёд»', score: 55.83, approval: 58.3, cost: 88, demo: true },
      ].sort((a, b) => b.score - a.score),
    };
    return D;
  }

  /* section labels for cherry-picking: every [data-sec] gets a chip "N·X Name"; toggle with the button or key L */
  function tags(board) {
    const style = document.createElement('style');
    style.textContent = `.kit-tag{position:absolute;top:10px;right:10px;z-index:40;font:600 11px/1.2 system-ui,Segoe UI,sans-serif;letter-spacing:.02em;
      background:#19b8d8;color:#04232b;padding:5px 8px;border-radius:6px;box-shadow:0 2px 8px rgba(0,0,0,.25);pointer-events:none}
      [data-sec]{position:relative} body.kit-off .kit-tag{display:none}
      .kit-toggle{position:fixed;left:12px;bottom:12px;z-index:9999;font:600 12px system-ui,sans-serif;background:#0b1120;color:#fff;border:1px solid #19b8d8;border-radius:8px;padding:8px 10px;cursor:pointer}
      .kit-back{position:fixed;left:12px;bottom:52px;z-index:9999;font:600 12px system-ui,sans-serif;background:#0b1120;color:#fff;border:1px solid #334155;border-radius:8px;padding:8px 10px;text-decoration:none}
      body.kit-shot .kit-toggle,body.kit-shot .kit-back{display:none}
      @media(max-width:700px){[data-sec]{overflow-x:auto;overflow-y:hidden}}`;
    document.head.appendChild(style);
    document.querySelectorAll('[data-sec]').forEach((el) => {
      const t = document.createElement('div');
      t.className = 'kit-tag';
      t.textContent = `${board}·${el.dataset.sec}`;
      el.appendChild(t);
    });
    const b = document.createElement('button');
    b.className = 'kit-toggle'; b.textContent = 'Метки секций: вкл (L)';
    b.onclick = () => { document.body.classList.toggle('kit-off'); b.textContent = 'Метки секций: ' + (document.body.classList.contains('kit-off') ? 'выкл' : 'вкл') + ' (L)'; };
    document.body.appendChild(b);
    const a = document.createElement('a'); a.className = 'kit-back'; a.href = './'; a.textContent = '← все 8 вариантов';
    document.body.appendChild(a);
    addEventListener('keydown', (e) => { if (e.key === 'l' || e.key === 'L' || e.key === 'д' || e.key === 'Д') b.click(); });
    if (new URLSearchParams(location.search).has('shot')) document.body.classList.add('kit-shot', 'kit-off');
  }

  /* svg helpers */
  function scale(v, a, b, c, d) { return c + ((v - a) / (b - a)) * (d - c); }
  function path(points, sx, sy) { return points.map((p, i) => `${i ? 'L' : 'M'}${sx(p[0]).toFixed(1)},${sy(p[1]).toFixed(1)}`).join(' '); }
  function stepPath(points, sx, sy) {
    let d = '';
    points.forEach((p, i) => { if (!i) d += `M${sx(p[0]).toFixed(1)},${sy(p[1]).toFixed(1)}`; else d += ` H${sx(p[0]).toFixed(1)} V${sy(p[1]).toFixed(1)}`; });
    return d;
  }

  window.KIT = { load, tags, num, int, sign, esc, unq, SHORT, QL, scale, path, stepPath, r2 };
})();
