/* Standalone classic script. Live: ctx.api('/api/stress', { decisions: [...] }).
   Mock: replay the engine's doc example, independent of the page's mock router. */
(function () {
  'use strict';

  const fixtureURL = document.currentScript && document.currentScript.src
    ? new URL('../mock/stress.json', document.currentScript.src).href
    : new URL('mock/stress.json', document.baseURI).href;
  const mounts = new WeakMap();
  const escape = (value) => String(value ?? '').replace(/[&<>"']/g,
    (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));

  // Reuse the host's global lexical helpers when present; fallbacks only format.
  const decimal = (value) => typeof num === 'function' ? num(value)
    : Number.isFinite(value) ? value.toFixed(2).replace('.', ',') : '—';
  const integer = (value) => typeof int === 'function' ? int(value)
    : Number.isFinite(value) ? value.toLocaleString('ru-RU') : '—';
  const signed = (value) => typeof sign === 'function' ? sign(value)
    : Number.isFinite(value) ? (value > 0 ? '+' : value === 0 ? '±' : '')
      + decimal(value).replace('-', '−') : '—';
  const prose = (value) => String(value ?? '').replace(/(\d)\.(?=\d)/g, '$1,');
  const decision = (value) => `${value.measure} · ${value.district || 'весь город'}`;

  const styles = `<style>
    .akim-stress .stress-row{display:grid;grid-template-columns:minmax(0,1.25fr) minmax(0,.5fr) minmax(0,.5fr) minmax(0,1fr) minmax(0,1.3fr);gap:16px;padding:16px;border-bottom:1px solid var(--rule);align-items:start}
    .akim-stress .stress-row:last-child{border-bottom:0}
    .akim-stress .stress-row > *{min-width:0}
    .akim-stress .stress-worst{box-shadow:inset 4px 0 var(--red);background:var(--paper)}
    .akim-stress .stress-combined{border-top:2px solid var(--ink);background:var(--steel)}
    .akim-stress .stress-row h3{margin:0;font:800 24px/1.05 "Sofia Sans Extra Condensed","Sofia Sans",sans-serif}
    .akim-stress .stress-row .lbl{display:block;margin-bottom:6px}
    .akim-stress .stress-cells{list-style:none;margin:0;padding:0;font-size:13px}
    .akim-stress .stress-cells li + li{margin-top:4px}
    .akim-stress .stress-plan-list{list-style:none;margin:0;padding:0}
    .akim-stress .stress-plan-list li{margin:0;padding:4px 8px}
    .akim-stress .stress-metrics{display:flex;flex-wrap:wrap;gap:20px 32px;margin:18px 0}
    .akim-stress .stress-metrics dd{margin:6px 0 0}
    .akim-stress .stress-detail{white-space:pre-wrap;overflow-wrap:anywhere}
    @media(max-width:767px){
      .akim-stress .stress-row{grid-template-columns:repeat(2,minmax(0,1fr));gap:14px}
      .akim-stress .stress-title,.akim-stress .stress-critical,.akim-stress .stress-insurance{grid-column:1/-1}
    }
  </style>`;

  function insuranceHTML(swap) {
    if (!swap) return '<span class="mut">Допустимой замены нет.</span>';
    const gain = escape(signed(swap.recovered));
    const arrow = '<span role="img" aria-label="на">→</span>';
    return `<div>Замените <b class="mono">${escape(swap.out)}</b> ${arrow}
      <b class="mono">${escape(swap.in.measure)}</b>:
      <b class="mono ${swap.recovered < 0 ? 'red' : 'peni'}" aria-label="Изменение Score: ${gain}">${gain}</b></div>
      <div class="mut mt-1 text-[13px]">${escape(swap.in.district || 'Весь город')} · Score после замены <span class="mono">${escape(decimal(swap.score))}</span></div>
      ${swap.recovered < 0 ? '<div class="red mt-1 text-[13px]">Даже лучшая замена снижает Score.</div>' : ''}`;
  }

  function scenarioHTML(scenario, worstEvent) {
    const worst = scenario.event_id === worstEvent;
    const combined = scenario.event_id === 'all';
    const district = scenario.district || Object.keys(scenario.effects).join(' · ');
    return `<article class="stress-row ${combined ? 'stress-combined' : ''} ${worst ? 'stress-worst' : ''}">
      <div class="stress-title">
        ${worst ? '<div class="lbl red">Худший отдельный кризис</div>' : ''}
        ${combined ? '<div class="lbl">Кризисы одновременно</div>' : ''}
        <h3>${escape(scenario.title)}</h3><div class="mut mt-1 text-[13px]">${escape(district)}</div>
      </div>
      <div><span class="lbl">Score при кризисе</span><b class="sm mono">${escape(decimal(scenario.score))}</b></div>
      <div><span class="lbl">Потеря Score</span><b class="sm mono red">${escape(decimal(scenario.loss))}</b></div>
      <div class="stress-critical"><span class="lbl">Новые критические ячейки</span>
        ${scenario.new_crit_cells.length ? `<ul class="stress-cells">${scenario.new_crit_cells.map((cell) =>
          `<li>${escape(cell.district)} · <span class="mono">${escape(cell.indicator)}</span>:
          <b class="mono red">${escape(decimal(cell.value))}</b></li>`).join('')}</ul>` : '<span class="mut text-[13px]">Нет</span>'}
      </div>
      <div class="stress-insurance text-[14px]"><span class="lbl">Страховочная замена</span>${insuranceHTML(scenario.insurance)}</div>
    </article>`;
  }

  function resultsHTML(data, mock) {
    const proof = data.crisis_proof_plan;
    return `<div class="paper strip">
      <p class="disp m-0 text-[28px] font-bold leading-tight">${escape(prose(data.verdict))}</p>
      <div class="mt-3 flex flex-wrap gap-x-6 gap-y-2 text-[14px]">
        <span>Score без кризисов: <b class="mono">${escape(decimal(data.score))}</b></span>
        <span>Место по устойчивости: <b class="mono peni">${escape(integer(data.robust_rank))}</b> из <span class="mono">${escape(integer(data.robust_total))}</span></span>
      </div>
      <p class="mut m-0 mt-2 text-[13px]">Место определяется по худшему отдельному кризису.</p>
      ${mock ? '<p class="mut m-0 mt-2 text-[13px]">Демо: записанный ответ движка для примера из ТЗ. Изменение плана не пересчитывает демо.</p>' : ''}
    </div>
    <div class="sheet-w mt-4">${data.scenarios.map((scenario) => scenarioHTML(scenario, data.worst_event)).join('')}</div>
    <section class="notice mt-4" style="border-top:4px solid var(--pen)">
      <h3>План, который выдерживает всё</h3>
      <p class="mut m-0 text-[14px]">Лучший план по худшему отдельному кризису. «Чёрная зима» показана отдельно.</p>
      <dl class="stress-metrics">
        <div><dt class="lbl">Score без кризисов</dt><dd class="mid">${escape(decimal(proof.score))}</dd></div>
        <div><dt class="lbl">Худший отдельный кризис</dt><dd class="mid peni">${escape(decimal(proof.worst_score))}</dd></div>
        <div><dt class="lbl">Чёрная зима</dt><dd class="mid">${escape(decimal(proof.all_score))}</dd></div>
        <div><dt class="lbl">Бюджет, ед.</dt><dd class="mid">${escape(integer(proof.cost))}</dd></div>
      </dl>
      <div class="flex flex-wrap items-center justify-between gap-4">
        <ul class="stress-plan-list mono flex flex-wrap gap-2 text-[12px]" aria-label="Решения устойчивого плана">
          ${proof.plan.decisions.map((item) => `<li class="border border-rule bg-paper px-2 py-1">${escape(decision(item))}</li>`).join('')}
        </ul>
        <button type="button" class="btn btn-pen" data-stress-apply>Загрузить этот план</button>
      </div>
      <p class="stress-detail m-0 mt-2 text-[14px]" data-stress-apply-status role="status"></p>
    </section>`;
  }

  async function errorDetail(error) {
    if (error && typeof error.json === 'function') {
      try { const body = await error.json(); return body.detail ?? error.statusText; } catch (_) { /* No JSON error body. */ }
    }
    return error?.detail ?? error?.response?.data?.detail ?? error?.message
      ?? (typeof error === 'string' ? error : 'Нет связи с сервером. Попробуйте ещё раз.');
  }

  const detailText = (detail) => typeof detail === 'string' ? detail : JSON.stringify(detail, null, 2);

  async function mount(el, ctx) {
    const token = {};
    mounts.set(el, token);
    el.innerHTML = `<section class="akim-stress" aria-label="Стресс-тест кризисами">${styles}
      <div class="hsec"><h2 class="h2">Стресс-тест кризисами</h2><p>Как план переживёт кризисы</p></div>
      <div data-stress-content aria-busy="true">
        <div class="plate p-6" role="status"><span class="spin" role="img" aria-label="Загрузка"></span>
          Проверяем план во всех кризисных сценариях…</div>
      </div></section>`;
    const content = el.querySelector('[data-stress-content]');
    const current = () => mounts.get(el) === token;
    try {
      let data;
      if (ctx.mock) {
        const response = await fetch(fixtureURL);
        if (!response.ok) throw response;
        data = await response.json();
      } else {
        data = await ctx.api('/api/stress', { decisions: ctx.plan?.decisions });
      }
      if (!current()) return;
      if (data?.detail != null) throw data;
      content.innerHTML = resultsHTML(data, ctx.mock);
      const button = content.querySelector('[data-stress-apply]');
      const status = content.querySelector('[data-stress-apply-status]');
      button.onclick = async () => {
        if (!current() || button.disabled) return;
        button.disabled = true;
        status.classList.remove('red');
        status.textContent = 'Загружаем план…';
        try {
          await ctx.onApplyPlan(JSON.parse(JSON.stringify(data.crisis_proof_plan.plan)));
          if (current()) status.textContent = 'Устойчивый план загружен.';
        } catch (error) {
          const detail = await errorDetail(error);
          if (current()) {
            status.classList.add('red');
            status.textContent = 'Не удалось загрузить план: ' + detailText(detail);
          }
        } finally {
          if (current()) button.disabled = false;
        }
      };
    } catch (error) {
      const detail = await errorDetail(error);
      if (!current()) return;
      const status = error?.status ?? error?.response?.status;
      const title = String(status) === '422' ? 'План не прошёл проверку'
        : String(status) === '500' ? 'Ошибка сервера при стресс-тесте' : 'Не удалось выполнить стресс-тест';
      content.innerHTML = `<div class="plate p-6">
        <div role="alert"><h3 class="sm red m-0">${title}</h3>
          <p class="stress-detail mut mt-2">${escape(detailText(detail))}</p></div>
        <button type="button" class="btn btn-line mt-2" data-stress-retry>Повторить</button>
      </div>`;
      content.querySelector('[data-stress-retry]').onclick = () => mount(el, ctx);
    } finally {
      if (current()) content.setAttribute('aria-busy', 'false');
    }
  }

  window.AkimPanels = window.AkimPanels || {};
  window.AkimPanels.stress = { mount };
}());
