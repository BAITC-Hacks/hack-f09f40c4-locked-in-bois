/* Mock backend for ?mock=1 and for offline fallback.
   Mirrors the API shapes the frontend expects (see web/API_REQUESTS.md).
   Works in the browser (window.MockEngine) and in Node (module.exports). */
(function (root) {
  'use strict';

  const QUARTER_LABELS = ['Старт', 'I кв. 2027', 'II кв. 2027', 'III кв. 2027', 'IV кв. 2027',
    'I кв. 2028', 'II кв. 2028', 'III кв. 2028', 'IV кв. 2028'];

  const r2 = (x) => Math.round(x * 100) / 100;
  const clamp = (x, a, b) => Math.max(a, Math.min(b, x));
  const sum = (a) => a.reduce((s, x) => s + x, 0);
  function plural(n, one, few, many) {
    const m10 = n % 10, m100 = n % 100;
    if (m10 === 1 && m100 !== 11) return one;
    if (m10 >= 2 && m10 <= 4 && (m100 < 10 || m100 >= 20)) return few;
    return many;
  }
  const fmt = (x) => r2(x).toFixed(2).replace('.', ',');

  function create(ds) {
    const dirs = ds.directions.map((d) => d.id);
    const dirName = Object.fromEntries(ds.directions.map((d) => [d.id, d.name]));
    const M = Object.fromEntries(ds.measures.map((m) => [m.id, m]));
    const DI = Object.fromEntries(ds.districts.map((d) => [d.id, d]));
    const totalPop = sum(ds.districts.map((d) => d.population));
    const T = ds.quarters || 8;
    let scale = 1;

    const norm = (decs) => (decs || []).filter((d) => d && d.measure_id)
      .map((d) => ({ measure_id: d.measure_id, district_id: d.district_id || null }));
    const label = (d) => {
      const m = M[d.measure_id];
      if (!m) return d.measure_id;
      return m.scope === 'district' && d.district_id && DI[d.district_id] ? `${m.name} (${DI[d.district_id].name})` : m.name;
    };

    // ---------- validation ----------
    function validate(decisions, opts) {
      const limit = (opts && opts.budget) || ds.budget;
      const decs = norm(decisions);
      const errors = [];
      const counts = Object.fromEntries(dirs.map((d) => [d, 0]));
      let cost = 0;
      const seen = new Set();
      for (const d of decs) {
        const m = M[d.measure_id];
        if (!m) { errors.push(`Неизвестная мера ${d.measure_id}`); continue; }
        cost += m.cost;
        counts[m.direction]++;
        if (seen.has(m.id)) errors.push(`«${m.name}» уже в плане`);
        seen.add(m.id);
        if (m.scope === 'district' && !d.district_id) errors.push(`«${m.name}»: выберите район`);
      }
      if (cost > limit) errors.push(`Перерасход бюджета: ${cost} из ${limit}. Уберите одну из дорогих мер.`);
      for (const k of dirs) if (counts[k] > ds.max_per_direction)
        errors.push(`«${dirName[k]}»: не более ${ds.max_per_direction} решений`);
      const missing = ds.slots - decs.length;
      if (missing > 0) errors.push(`Выберите ещё ${missing} ${plural(missing, 'решение', 'решения', 'решений')}`);
      if (missing < 0) errors.push(`Не более ${ds.slots} решений`);
      const valid = errors.length === 0;
      return {
        valid, errors,
        budget_used: cost, budget_limit: limit, budget_left: limit - cost,
        direction_counts: counts,
        message: valid
          ? `План готов. Остаток ${limit - cost} ед. не сгорает и не даёт бонуса.`
          : errors[0],
      };
    }

    // ---------- simulation ----------
    const frac = (t, lag) => clamp((t - lag + 1) / 2, 0, 1);

    function rawGains(decs, mods) {
      const counts = {};
      for (const d of decs) { const m = M[d.measure_id]; counts[m.direction] = (counts[m.direction] || 0) + 1; }
      return decs.map((d) => {
        const m = M[d.measure_id];
        const overlap = counts[m.direction] > 1 ? 0.9 : 1;
        const targets = m.scope === 'city' ? ds.districts.map((x) => x.id) : [d.district_id];
        const g = {};
        for (const k of targets) {
          if (!DI[k]) continue;
          g[k] = {};
          for (const [dir, a] of Object.entries(m.effects)) {
            let mult = 1;
            if (mods && mods.mult && mods.mult[k] && mods.mult[k][dir]) mult = mods.mult[k][dir];
            const base = DI[k].indices[dir];
            g[k][dir] = a * overlap * mult * (1 - base / 100) * 1.6;
          }
        }
        return { d, m, g };
      });
    }

    function aqlsOf(idxByDistrict) {
      return sum(ds.districts.map((x) => x.population * sum(dirs.map((k) => idxByDistrict[x.id][k])) / dirs.length)) / totalPop;
    }

    function stateAt(gains, t, only) {
      const idx = {};
      for (const x of ds.districts) idx[x.id] = { ...x.indices };
      gains.forEach((row, i) => {
        if (only && !only(i)) return;
        const f = frac(t, row.m.lag);
        for (const [k, byDir] of Object.entries(row.g))
          for (const [dir, v] of Object.entries(byDir)) idx[k][dir] = clamp(idx[k][dir] + v * f * scale, 0, 100);
      });
      return idx;
    }

    const BASE = r2(aqlsOf(Object.fromEntries(ds.districts.map((x) => [x.id, x.indices]))));

    function core(decisions, mods) {
      const decs = norm(decisions).filter((d) => M[d.measure_id]);
      const gains = rawGains(decs, mods);
      const timeline = [];
      for (let t = 0; t <= T; t++) timeline.push({ quarter: t, label: QUARTER_LABELS[t] || `Q${t}`, aqls: r2(aqlsOf(stateAt(gains, t))) });
      const finalIdx = stateAt(gains, T);
      const aqls = r2(aqlsOf(finalIdx));
      const districts = ds.districts.map((x) => {
        const before = r2(sum(dirs.map((k) => x.indices[k])) / dirs.length);
        const after = r2(sum(dirs.map((k) => finalIdx[x.id][k])) / dirs.length);
        return {
          id: x.id, name: x.name, before, after, delta: r2(after - before),
          indices_before: { ...x.indices },
          indices_after: Object.fromEntries(dirs.map((k) => [k, r2(finalIdx[x.id][k])])),
        };
      });
      const g4 = timeline[4].aqls - BASE, g8 = aqls - BASE;
      const helped = districts.filter((x) => x.delta >= 1).length;
      const red = districts.filter((x) => x.after < 40).length;
      // voters punish pouring everything into one district
      const concentration = Math.max(0, Math.max(...districts.map((x) => x.delta)) - 6);
      const approval = r2(clamp(40 + 5 * g4 + 1.2 * g8 + 2.2 * helped - 6 * red - 1.5 * concentration, 0, 100));
      return { decs, gains, timeline, aqls, districts, approval };
    }

    // ---------- enumeration for regret / pareto ----------
    let ENUM = null;
    function combos(arr, k, start = 0, acc = [], out = []) {
      if (acc.length === k) { out.push(acc.slice()); return out; }
      for (let i = start; i < arr.length; i++) { acc.push(arr[i]); combos(arr, k, i + 1, acc, out); acc.pop(); }
      return out;
    }
    function enumerate() {
      if (ENUM) return ENUM;
      const unit = {};
      for (const m of ds.measures) {
        const opts = m.scope === 'city' ? [null] : ds.districts.map((x) => x.id);
        unit[m.id] = opts.map((k) => {
          const rows = rawGains([{ measure_id: m.id, district_id: k }]);
          let g = 0;
          for (const [dk, byDir] of Object.entries(rows[0].g))
            g += DI[dk].population * sum(Object.values(byDir)) / dirs.length / totalPop;
          return { district_id: k, g };
        });
      }
      const scores = [];
      const plans = [];
      for (const c of combos(ds.measures, ds.slots)) {
        const cost = sum(c.map((m) => m.cost));
        if (cost > ds.budget) continue;
        const cnt = {};
        let ok = true;
        for (const m of c) { cnt[m.direction] = (cnt[m.direction] || 0) + 1; if (cnt[m.direction] > ds.max_per_direction) ok = false; }
        if (!ok) continue;
        const lists = c.map((m) => unit[m.id].map((u) => ({ m, u, f: cnt[m.direction] > 1 ? 0.9 : 1 })));
        const walk = (i, g, pick) => {
          if (i === lists.length) { scores.push(g); plans.push({ cost, g, pick: pick.slice() }); return; }
          for (const o of lists[i]) { pick.push(o); walk(i + 1, g + o.u.g * o.f, pick); pick.pop(); }
        };
        walk(0, 0, []);
      }
      let rawMax = 0;
      for (const s of scores) if (s > rawMax) rawMax = s;
      scale = (ds.max_aqls - BASE) / rawMax;
      const sorted = Float64Array.from(scores.map((s) => s * scale)).sort().reverse();
      const byCost = {};
      for (const p of plans) byCost[p.cost] = Math.max(byCost[p.cost] || 0, p.g * scale);
      const costs = Object.keys(byCost).map(Number).sort((a, b) => a - b);
      const pareto = [];
      let best = -1;
      for (const c of costs) if (byCost[c] > best) { best = byCost[c]; pareto.push({ cost: c, best: r2(BASE + best) }); }
      plans.sort((a, b) => b.g - a.g);
      const top = plans.slice(0, 400).map((p) => p.pick.map((o) => ({ measure_id: o.m.id, district_id: o.u.district_id })));
      ENUM = { sorted, pareto, top, count: sorted.length };
      return ENUM;
    }
    enumerate();

    function rankOf(aqls) {
      const E = enumerate();
      const g = aqls - BASE;
      let lo = 0, hi = E.sorted.length;
      while (lo < hi) { const mid = (lo + hi) >> 1; if (E.sorted[mid] > g + 0.005) lo = mid + 1; else hi = mid; }
      const f = lo / E.sorted.length;
      const rank = Math.max(1, Math.round(f * ds.total_plans) + 1);
      return { rank, total_plans: ds.total_plans, top_percent: r2(Math.max(0.01, (rank / ds.total_plans) * 100)) };
    }

    // ---------- analysis text ----------
    function contributions(c) {
      return c.decs.map((d, i) => {
        const without = aqlsOf(stateAt(c.gains, T, (j) => j !== i));
        return { measure_id: d.measure_id, district_id: d.district_id, name: label(d), delta: r2(c.aqls - without) };
      }).sort((a, b) => b.delta - a.delta);
    }

    const same = (a, b) => a.measure_id === b.measure_id && (a.district_id || null) === (b.district_id || null);

    // all single-decision replacements of a plan that stay valid
    function neighbours(plan) {
      const out = [];
      plan.forEach((_, i) => {
        for (const m of ds.measures) {
          const opts = m.scope === 'city' ? [null] : ds.districts.map((x) => x.id);
          for (const k of opts) {
            const next = plan.slice();
            next[i] = { measure_id: m.id, district_id: k };
            if (validate(next).valid) out.push(next);
          }
        }
      });
      return out;
    }
    const objective = (v) => (v.approval >= ds.reelection_threshold ? 1000 : 0) + v.aqls + 0.01 * v.approval;

    function recommend(c) {
      // greedy two-step hill climb from the user's own plan: at most two swaps, must keep the akim in office
      let cur = { plan: c.decs, v: c, score: objective(c) };
      for (let step = 0; step < 2; step++) {
        let best = cur;
        for (const plan of neighbours(cur.plan)) {
          const v = core(plan);
          const s = objective(v);
          if (s > best.score + 1e-9) best = { plan, v, score: s };
        }
        if (best === cur) break;
        cur = best;
      }
      const best = { plan: cur.plan, aqls: cur.v.aqls, approval: cur.v.approval };
      const userDecs = c.decs;
      const out = userDecs.filter((d) => !best.plan.some((p) => same(p, d)));
      const inn = best.plan.filter((p) => !userDecs.some((d) => same(p, d)));
      if (!inn.length) return {
        text: 'Ваш план уже на границе лучшего: сильнее не сделать без потери рейтинга. Держите курс.',
        decisions: userDecs, aqls: c.aqls, approval: c.approval,
      };
      return {
        text: `Замените ${out.map((d) => `«${label(d)}»`).join(', ')} на ${inn.map((d) => `«${label(d)}»`).join(', ')}. ` +
          `Прогноз: AQLS ${fmt(best.aqls)}, рейтинг ${Math.round(best.approval)}%` +
          (best.approval >= ds.reelection_threshold ? ' — и город растёт, и аким остаётся.' : ' — лучше, но до порога переизбрания всё ещё не хватает.'),
        decisions: best.plan, aqls: best.aqls, approval: best.approval,
      };
    }

    function analysis(c, contrib, v) {
      const strengths = [], risks = [], consequences = [], tradeoffs = [];
      contrib.slice(0, 2).forEach((x) => strengths.push(`«${x.name}» даёт +${fmt(x.delta)} к AQLS — главный вклад плана.`));
      v.districts.filter((x) => x.before < 40 && x.after >= 40)
        .forEach((x) => strengths.push(`${x.name} выходит из красной зоны: ${fmt(x.before)} → ${fmt(x.after)}.`));
      c.decs.forEach((d) => {
        const m = M[d.measure_id];
        if (m.lag >= 5) risks.push(`Эффект «${m.name}» придёт только к ${QUARTER_LABELS[Math.min(T, m.lag + 1)]} — избиратели почти не успеют его увидеть.`);
      });
      v.districts.filter((x) => x.after < 40).forEach((x) => risks.push(`${x.name} остаётся ниже 40 (${fmt(x.after)}) — район копит протест.`));
      if (v.budget_left >= 15) risks.push(`${v.budget_left} ед. бюджета не работают: остаток не сгорает, но и бонуса не даёт.`);
      if (!risks.length) risks.push('Явных провалов нет, но план не оставляет запаса на кризис.');
      consequences.push(v.reelected
        ? `Рейтинг ${Math.round(v.approval)}% — выше порога ${ds.reelection_threshold}%. Аким идёт на второй срок.`
        : `Рейтинг ${Math.round(v.approval)}% — ниже порога ${ds.reelection_threshold}%. Маслихат ставит вопрос об отставке.`);
      consequences.push(`К IV кв. 2028 AQLS города ${v.delta >= 0 ? 'растёт' : 'падает'} до ${fmt(v.aqls)} (${v.delta >= 0 ? '+' : ''}${fmt(v.delta)}).`);
      const worst = contrib[contrib.length - 1];
      if (worst) tradeoffs.push(`«${worst.name}» — самая слабая мера: ${M[worst.measure_id].cost} ед. за +${fmt(worst.delta)}.`);
      const slow = c.decs.map((d) => M[d.measure_id]).filter((m) => m.lag >= 5);
      tradeoffs.push(slow.length
        ? 'Долгие проекты поднимают итог, но не рейтинг: результат достанется уже следующему акиму.'
        : 'Быстрые меры покупают рейтинг, но потолок AQLS ниже: большая инфраструктура откладывается.');
      return { strengths, risks, consequences, tradeoffs, recommendation: recommend(c) };
    }

    function verdict(decisions, mods, opts) {
      const c = core(decisions, mods);
      const val = validate(decisions, opts);
      const contrib = contributions(c);
      const v = {
        aqls: c.aqls, baseline: ds.baseline_aqls, delta: r2(c.aqls - ds.baseline_aqls), max_aqls: ds.max_aqls,
        approval: c.approval, reelection_threshold: ds.reelection_threshold,
        reelected: c.approval >= ds.reelection_threshold,
        ...rankOf(c.aqls),
        budget_used: val.budget_used, budget_left: val.budget_left,
        decisions: c.decs,
        districts: c.districts, timeline: c.timeline, contributions: contrib,
        pareto: enumerate().pareto, user_point: { cost: val.budget_used, aqls: c.aqls },
        mode: 'mock',
      };
      v.analysis = analysis(c, contrib, v);
      return v;
    }

    // ---------- narrative ----------
    function narrative(decisions, v) {
      v = v || verdict(decisions);
      const up = v.districts.slice().sort((a, b) => b.delta - a.delta);
      const winner = up[0], loser = up[up.length - 1];
      const council = ds.districts.map((x) => {
        const d = v.districts.find((y) => y.id === x.id);
        let mood, reaction;
        if (d.delta >= 1.5) { mood = 'support'; reaction = `«${x.name} наконец увидел деньги: +${fmt(d.delta)}. Голосую за».`; }
        else if (d.delta >= 0.5) { mood = 'neutral'; reaction = `«Стало лучше на ${fmt(d.delta)}, но мои избиратели ждали большего».`; }
        else { mood = 'oppose'; reaction = `«Району ${x.name} — почти ничего (+${fmt(d.delta)}). Бюджет ушёл мимо нас».`; }
        return { district_id: x.id, district: x.name, deputy: x.deputy.name, initials: x.deputy.initials, mood, reaction };
      });
      const critical = v.districts
        .filter((x) => x.after < 40 || Object.values(x.indices_after).some((i) => i < 40))
        .map((x) => {
          const low = Object.entries(x.indices_after).filter(([, i]) => i < 40).map(([k, i]) => `${dirName[k].toLowerCase()} ${fmt(i)}`);
          return { district: x.name, aqls: x.after, note: low.length ? `Ниже 40: ${low.join(', ')}` : `Индекс района ${fmt(x.after)}` };
        });
      const headline = v.reelected
        ? (v.delta >= 3 ? `Столица выросла до ${fmt(v.aqls)}. Аким остаётся` : 'Скромный рост, спокойные выборы: аким удержал кресло')
        : `Город вырос, доверие — нет: рейтинг акима ${Math.round(v.approval)}%`;
      const names = v.decisions.map((d) => `«${label(d)}»`);
      return {
        mode: 'mock',
        newspaper: {
          masthead: 'Астана Times', edition: 'IV квартал 2028', issue: '№ 208',
          headline,
          subheadline: `Индекс качества жизни — ${fmt(v.aqls)} против ${fmt(v.baseline)} два года назад. ` +
            `Решения акима — ${v.rank.toLocaleString('ru-RU')}-е из ${v.total_plans.toLocaleString('ru-RU')} возможных.`,
          lead: {
            title: 'Итоги двух лет: на что ушли 100 единиц',
            body: [
              `Два года назад аким выбрал пять решений: ${names.join(', ')}. Потрачено ${v.budget_used} единиц бюджета из 100.`,
              `Больше всех выиграл район ${winner.name}: +${fmt(winner.delta)}. Меньше всех — ${loser.name}: +${fmt(loser.delta)}. ` +
              `Средний индекс столицы — ${fmt(v.aqls)}.`,
              v.reelected
                ? `Рейтинг ${Math.round(v.approval)}% — выборы акиму не страшны. Вопрос в том, хватит ли запаса на следующий кризис.`
                : `Рейтинг ${Math.round(v.approval)}% — ниже порога в ${v.reelection_threshold}%. Долгие проекты не успели дать результат к выборам.`,
            ],
          },
          articles: [
            { title: `${winner.name}: район-победитель`, body: `Индекс района вырос с ${fmt(winner.before)} до ${fmt(winner.after)}. Жители замечают перемены во дворах и поликлиниках.` },
            { title: `${loser.name} ждёт своей очереди`, body: `Здесь индекс почти не изменился: ${fmt(loser.before)} → ${fmt(loser.after)}. Депутат района требует пересмотреть бюджет.` },
          ],
          editorial: {
            title: 'Колонка редактора: оптимум не голосует',
            body: `Лучший план на бумаге дал бы городу ${fmt(v.max_aqls)}. Но избиратель судит по тому, что видит до выборов. ` +
              'Хорошая политика — это не максимум индекса, а максимум, который успеваешь показать.',
          },
          critical_zones: critical,
          stats: [
            { label: 'AQLS', value: fmt(v.aqls) },
            { label: 'Рейтинг акима', value: `${Math.round(v.approval)}%` },
            { label: 'Бюджет', value: `${v.budget_used}/100` },
          ],
        },
        council,
      };
    }

    // ---------- shock ----------
    const SHOCK = {
      id: 'flood-2028', title: 'Весенний паводок на Есиле',
      quarter: 'II кв. 2028 · 5-й час',
      description: 'Вода подтопила левый берег. Республика забирает 15 единиц бюджета на ликвидацию последствий, а ЖКХ и больницы в Нуре и Есиле становятся приоритетом.',
      budget_limit: 85,
      effects: ['Лимит бюджета: 100 → 85', 'ЖКХ и здравоохранение в Нуре и Есиле ×1,5', 'Нужно заменить ровно одно решение'],
      mods: { mult: { nura: { housing: 1.5, health: 1.5 }, yesil: { housing: 1.5, health: 1.5 } } },
    };
    function shock() { const { mods, ...pub } = SHOCK; return pub; }
    function resolve(body) {
      const decs = norm(body.decisions);
      const idx = body.remove_index;
      if (idx == null || idx < 0 || idx >= decs.length || !body.add || !body.add.measure_id)
        return { ok: false, validation: { valid: false, errors: ['Выберите, что убрать и что добавить'], message: 'Выберите, что убрать и что добавить' } };
      const next = decs.slice();
      next[idx] = { measure_id: body.add.measure_id, district_id: body.add.district_id || null };
      const val = validate(next, { budget: SHOCK.budget_limit });
      if (!val.valid) return { ok: false, validation: val };
      const before = verdict(decs, SHOCK.mods, { budget: SHOCK.budget_limit });
      const after = verdict(next, SHOCK.mods, { budget: SHOCK.budget_limit });
      const outL = label(decs[idx]), inL = label(next[idx]);
      const dA = after.aqls - before.aqls, dR = after.approval - before.approval;
      const comment = `Вы заменили «${outL}» на «${inL}». AQLS ${fmt(before.aqls)} → ${fmt(after.aqls)} (${dA >= 0 ? '+' : ''}${fmt(dA)}), ` +
        `рейтинг ${Math.round(before.approval)}% → ${Math.round(after.approval)}%. ` +
        (dR >= 0 ? 'Кризис стал поводом показать заботу — горожане это заметили.' : 'Экономия сработала, но жители почувствовали, что их бросили.');
      return { ok: true, decisions: next, validation: val, before, verdict: after, comment };
    }

    return { dataset: ds, validate, verdict, narrative, shock, resolve, label, BASE, enumerate };
  }

  const api = { create, QUARTER_LABELS };
  if (typeof module !== 'undefined' && module.exports) module.exports = api;
  else root.MockEngine = api;
})(typeof window !== 'undefined' ? window : globalThis);
