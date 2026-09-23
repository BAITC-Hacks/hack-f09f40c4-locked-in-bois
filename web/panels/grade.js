/* Standalone classic script. Live transport: ctx.api('/api/grade', ctx.plan).
 * Mock transport replays the engine fixture relative to this script.
 * mount returns a cleanup function; remounting also disposes the old chart.
 * Scores, losses, classifications and prose come exclusively from grade(). */
(function () {
  'use strict';

  const instances = new WeakMap();
  const fixtureURL = new URL(document.currentScript && document.currentScript.src
    ? '../mock/grade.json' : 'mock/grade.json',
  document.currentScript && document.currentScript.src || document.baseURI);
  const escape = (value) => String(value ?? '').replace(/[&<>"']/g,
    (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));
  // The page's classic-script lexical helpers are accessible without window.num.
  const number = (value) => typeof num === 'function' ? num(value)
    : typeof value === 'number' && Number.isFinite(value) ? value.toFixed(2).replace('.', ',') : '—';
  const prose = (value) => typeof ruProse === 'function' ? ruProse(String(value ?? ''))
    : String(value ?? '').replace(/(\d)\.(?=\d)/g, '$1,');
  const tone = (grade) => ({ best: 'var(--pen-ink)', excellent: 'var(--pen-ink)',
    good: 'var(--ink)', inaccuracy: 'var(--red-ink)', mistake: 'var(--red-ink)',
    blunder: 'var(--red-ink)', sacrifice: 'var(--graph)' }[grade] || 'var(--graph)');
  const decision = (move) => `${move.measure} · ${move.name} · ${move.district || 'весь город'}`;
  const symbol = (move) => move.symbol ? `<span class="mono" role="img" aria-label="${escape(move.label)}"
    style="color:${tone(move.grade)};font-size:28px;font-weight:600">${escape(move.symbol)}</span>` : '';

  const styles = `<style>
    .akim-grade .grade-overview{display:grid;grid-template-columns:minmax(0,1fr) 260px;gap:12px}
    .akim-grade .grade-chart{position:relative;height:150px;margin-top:12px}
    .akim-grade .grade-steps{display:flex;flex-wrap:wrap;gap:8px;margin-top:12px}
    .akim-grade .grade-step{flex:1 1 86px;min-width:0;padding:8px;background:var(--white);border:1px solid var(--rule)}
    .akim-grade .grade-moves{list-style:none;padding:0;margin:0;border:1px solid var(--rule);background:var(--white)}
    .akim-grade .grade-move{display:grid;grid-template-columns:52px minmax(0,1.2fr) 104px minmax(0,1fr);gap:10px 16px;padding:16px;border-top:1px solid var(--rule)}
    .akim-grade .grade-move:first-child{border-top:0}
    .akim-grade .grade-move[data-worst="true"]{box-shadow:inset 4px 0 var(--red)}
    .akim-grade .grade-comment{grid-column:2/-1;margin:0;font-size:13.5px;line-height:1.45;color:var(--graph)}
    .akim-grade .grade-alternative{font-size:13.5px;line-height:1.4}
    .akim-grade .grade-loss{text-align:right}
    .akim-grade .grade-copy{overflow-wrap:anywhere;min-width:0}
    .akim-grade .grade-highlight{border-left:4px solid var(--red);margin-top:12px}
    @media(max-width:767px){
      .akim-grade .grade-overview{grid-template-columns:minmax(0,1fr)}
      .akim-grade .grade-accuracy{grid-row:1;display:flex;flex-wrap:wrap;align-items:center;justify-content:space-between;gap:14px}
      .akim-grade .grade-move{grid-template-columns:36px minmax(0,1fr) 86px;padding:12px;gap:8px}
      .akim-grade .grade-alternative{grid-column:2/-1}
      .akim-grade .grade-comment{grid-column:2/-1}
    }
  </style>`;

  function moveRow(move, worst) {
    const alternative = move.best_alternative;
    return `<li class="grade-move" data-worst="${worst && worst.index === move.index ? 'true' : 'false'}">
      <div><div class="mono mut" style="font-size:11px">${escape(move.index)}</div>${symbol(move)}</div>
      <div class="grade-copy"><div class="lbl" style="color:${tone(move.grade)}">${escape(move.label)}</div>
        <h3 class="sm" style="margin:5px 0">${escape(move.measure)} · ${escape(move.name)}</h3>
        <div class="mut" style="font-size:13px">${escape(move.district || 'весь город')}</div></div>
      <div class="grade-loss"><div class="lbl">Потеря</div><div class="sm mono" style="margin-top:8px;color:${tone(move.grade)}">${escape(number(move.loss))}</div>
        <div class="mut" style="font-size:12px">балла Score</div></div>
      <div class="grade-alternative grade-copy"><div class="lbl" style="margin-bottom:6px">Лучшая альтернатива</div>
        ${alternative ? `${escape(decision(alternative))}<div class="mono peni" style="margin-top:4px">Score ${escape(number(alternative.score))}</div>`
          : '<span class="mut">Текущий ход уже лучший</span>'}</div>
      <p class="grade-comment grade-copy">${escape(prose(move.comment))}</p>
    </li>`;
  }

  function render(data, mock) {
    const worst = data.biggest_blunder;
    return `<div class="hsec"><h2 class="h2">Разбор партии</h2><p>Цена каждого решения</p></div>
      ${mock ? '<p class="tape" style="margin:0 0 12px">Демо: записанный разбор примера из ТЗ.</p>' : ''}
      <div class="grade-overview">
        <section class="paper strip" aria-label="Достижимый потолок">
          <h3 class="lbl" style="margin:0">Достижимый потолок Score</h3>
          <p class="mut" style="font-size:13px;margin:6px 0 0">Лучший итог, пока уже сделанные ходы остаются в плане.</p>
          <div class="grade-chart"><canvas role="img" aria-label="Достижимый потолок по шагам; точные значения приведены под графиком"></canvas></div>
          <div class="grade-steps" role="list" aria-label="Потолок после каждого шага">${data.eval_bar.map((step) => `
            <div class="grade-step" role="listitem"${step.dead_end ? ' style="border-color:var(--red)"' : ''}>
              <div class="lbl">Шаг ${escape(step.step)}</div>
              <div class="mono ${step.dead_end ? 'red' : ''}" style="font-weight:600;margin-top:4px">${step.dead_end
                ? '<span role="img" aria-label="Тупик">×</span> Тупик' : escape(number(step.ceiling))}</div>
              ${step.drop != null ? `<div class="mut" style="font-size:12px">потеря ${escape(number(step.drop))}</div>` : ''}
              ${step.reason ? `<div class="red grade-copy" style="font-size:12px">${escape(prose(step.reason))}</div>` : ''}
            </div>`).join('')}</div>
        </section>
        <section class="plate dialbox grade-accuracy" aria-label="Точность партии">
          <div><h3 class="lbl" style="margin:0 0 14px">Точность партии</h3>
            <div class="big peni" style="font-size:72px">${escape(number(data.accuracy))}<span style="font-size:32px">%</span></div></div>
          <div style="margin-top:18px"><div class="lbl">Итоговый Score</div><div class="mid" style="margin-top:6px">${escape(number(data.score))}</div></div>
        </section>
      </div>
      <p class="disp grade-copy" style="font-size:26px;font-weight:700;line-height:1.15;margin:16px 0">${escape(prose(data.summary))}</p>
      ${worst ? `<aside class="notice grade-highlight"><h3>Главная потеря партии · ход ${escape(worst.index)}</h3>
        <div class="grade-copy">${symbol(worst)} <b>${escape(worst.label)} · ${escape(decision(worst))}</b>
          <span class="mono red"> · потеря ${escape(number(worst.loss))} балла Score</span></div>
        <p class="mut grade-copy" style="margin:6px 0 0">${escape(prose(worst.comment))}</p></aside>`
        : '<p class="tape peni">Неточностей, ошибок и зевков нет.</p>'}
      <section style="margin-top:24px" aria-label="Разбор ходов">
        <div class="hsec"><h3 class="h2">Ходы партии</h3><p>Упущенные баллы относительно лучшей замены; остальные решения фиксированы</p></div>
        <ol class="grade-moves">${data.moves.map((move) => moveRow(move, worst)).join('')}</ol>
        <p class="mut" style="font-size:12.5px;margin:10px 0 0">Потери отдельных ходов не складываются в отставание от оптимума. График показывает потолок в порядке выбора мер; итоговый Score от порядка не зависит.</p>
      </section>`;
  }

  function drawChart(root, steps) {
    const canvas = root.querySelector('canvas');
    if (typeof window.Chart !== 'function') {
      canvas.parentElement.hidden = true;
      return null; // The visible, accessible step strip still carries every value.
    }
    const css = getComputedStyle(root);
    const color = (name) => css.getPropertyValue(name).trim();
    const font = { family: '"JetBrains Mono", ui-monospace, monospace', size: 11 };
    return new window.Chart(canvas, {
      type: 'line',
      data: { labels: steps.map((step) => `Шаг ${step.step}`), datasets: [{
        label: 'Достижимый потолок', data: steps.map((step) => step.ceiling),
        stepped: 'before', spanGaps: false, fill: false, borderWidth: 3,
        borderColor: color('--pen'), pointBackgroundColor: color('--ink'),
        pointBorderColor: color('--pen'), pointBorderWidth: 2, pointRadius: 4, pointHoverRadius: 6,
      }] },
      // Missing ceilings stay null. A cross at the foot marks a dead end without
      // inventing a Score coordinate or joining the gap to a neighbouring point.
      plugins: [{ id: 'akimGradeDeadEnds', afterDraw(chart) {
        const drawing = chart.ctx;
        drawing.save(); drawing.strokeStyle = color('--red'); drawing.lineWidth = 2;
        steps.forEach((step, index) => {
          if (!step.dead_end) return;
          const x = chart.scales.x.getPixelForValue(index), y = chart.chartArea.bottom - 8;
          drawing.beginPath(); drawing.moveTo(x - 4, y - 4); drawing.lineTo(x + 4, y + 4);
          drawing.moveTo(x + 4, y - 4); drawing.lineTo(x - 4, y + 4); drawing.stroke();
        });
        drawing.restore();
      } }],
      options: { responsive: true, maintainAspectRatio: false, animation: false,
        layout: { padding: 8 },
        plugins: { legend: { display: false }, tooltip: {
          backgroundColor: color('--ink'), titleColor: color('--pen'), bodyColor: color('--white'),
          cornerRadius: 0, padding: 10, displayColors: false, titleFont: font, bodyFont: font,
          callbacks: {
            label: (context) => `Потолок: ${number(steps[context.dataIndex].ceiling)}`,
            afterLabel: (context) => {
              const step = steps[context.dataIndex];
              return [step.decision ? decision(step.decision) : '',
                step.drop != null ? `Потеря потолка: ${number(step.drop)}` : '', step.reason || ''].filter(Boolean);
            },
          },
        } },
        scales: {
          x: { grid: { display: false }, border: { color: color('--ink') },
            ticks: { color: color('--graph'), font, maxRotation: 0 } },
          // Do not display Chart.js-generated numeric ticks: values are below.
          y: { display: false },
        },
      },
    });
  }

  function errorDetail(error) {
    const detail = error && (error.detail ?? error.data?.detail ?? error.body?.detail ?? error.message);
    if (typeof detail === 'string') return detail;
    if (detail != null) return JSON.stringify(detail);
    return 'Не удалось получить ответ. Проверьте соединение и повторите попытку.';
  }

  async function getReview(ctx, signal) {
    if (!ctx.mock) return ctx.api('/api/grade', ctx.plan);
    const response = await fetch(fixtureURL, { signal });
    const body = await response.json();
    if (!response.ok) throw { status: response.status, detail: body.detail };
    return body;
  }

  function mount(el, ctx) {
    const previous = instances.get(el);
    if (previous) previous();
    let active = true, chart = null, request = null;
    const root = document.createElement('div');
    root.className = 'akim-grade';
    root.lang = 'ru';
    el.replaceChildren(root);

    async function load() {
      if (!active) return;
      if (request) request.abort();
      request = new AbortController();
      const current = request;
      if (chart) { chart.destroy(); chart = null; }
      root.setAttribute('aria-busy', 'true');
      root.innerHTML = `${styles}<h2 class="h2" style="margin-bottom:16px">Разбор партии</h2>
        <div class="plate dialbox" role="status"><span class="spin" aria-hidden="true"></span> Разбираем решения и проверяем достижимый потолок…</div>`;
      try {
        const data = await getReview(ctx, current.signal);
        if (!active || current !== request) return;
        if (!data || !Array.isArray(data.moves) || !Array.isArray(data.eval_bar)) {
          throw { detail: data?.detail || 'Сервер вернул неполный разбор партии.' };
        }
        root.innerHTML = styles + render(data, ctx.mock);
        // A CDN/canvas failure should leave the engine's textual review usable.
        try { chart = drawChart(root, data.eval_bar); }
        catch (_) {
          const canvas = root.querySelector('canvas');
          window.Chart?.getChart?.(canvas)?.destroy();
          canvas.parentElement.hidden = true;
        }
      } catch (error) {
        if (!active || current !== request) return;
        const status = error && (error.status ?? error.response?.status);
        const title = String(status) === '422' ? 'План не прошёл проверку'
          : String(status) === '500' ? 'Ошибка сервера при разборе партии' : 'Разбор партии недоступен';
        root.innerHTML = `${styles}<div class="plate dialbox"><div role="alert"><h2 class="h2 red">${title}</h2>
          <p class="grade-copy">${escape(errorDetail(error))}</p></div>
          <button class="btn btn-ink" type="button">Повторить разбор</button></div>`;
        root.querySelector('button').onclick = load;
      } finally {
        if (active && current === request) root.setAttribute('aria-busy', 'false');
      }
    }

    function dispose() {
      if (!active) return;
      active = false;
      if (request) request.abort();
      if (chart) chart.destroy();
      instances.delete(el);
    }
    instances.set(el, dispose);
    void load();
    return dispose;
  }

  window.AkimPanels = window.AkimPanels || {};
  window.AkimPanels.grade = { mount };
}());
