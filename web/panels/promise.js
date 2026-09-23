(function () {
  'use strict';

  // Live requests use ctx.api(path, body); an omitted body means GET.
  // The static demo replays one real engine response, without simulating prices.
  const mockBase = new URL('../mock/', document.currentScript?.src || new URL('panels/promise.js', document.baseURI));
  const mounts = new WeakMap();
  const escape = (value) => String(value ?? '').replace(/[&<>"']/g, (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));
  const number = (value) => typeof num === 'function' ? num(value, 2)
    : value == null ? '—' : Number(value).toFixed(2).replace('.', ',');
  const integer = (value) => typeof int === 'function' ? int(value)
    : value == null ? '—' : Number(value).toLocaleString('ru-RU');
  const prose = (value) => String(value ?? '').replace(/(\d)\.(?=\d)/g, '$1,');
  // Catalog/response rows carry presentation fields which the engine rejects
  // in requests. Send only the documented promise constraint fields.
  const constraint = (row) => Object.fromEntries(['type', 'measure', 'district', 'value']
    .filter((key) => Object.prototype.hasOwnProperty.call(row, key)).map((key) => [key, row[key]]));
  const keyOf = (row) => JSON.stringify(constraint(row));
  const clone = (value) => JSON.parse(JSON.stringify(value));

  async function fixture(name) {
    const response = await fetch(new URL(name + '.json', mockBase));
    const data = await response.json();
    if (!response.ok) throw { status: response.status, detail: data.detail };
    return data;
  }

  function mount(el, ctx) {
    mounts.get(el)?.destroy();
    let alive = true, sequence = 0, catalog = [], dataset, saved;
    let selected = new Map(), custom = new Map();
    const root = document.createElement('section');
    root.className = 'akim-promise';
    root.setAttribute('aria-label', 'Цена обещания');
    root.innerHTML = `
      <style>
        .akim-promise .promise-layout{display:grid;gap:24px;grid-template-columns:minmax(0,1fr)}
        .akim-promise .promise-pad{padding:18px}
        .akim-promise .promise-stack{display:grid;gap:12px;min-width:0}
        .akim-promise .promise-choices{display:grid;gap:8px}
        .akim-promise .promise-choice{display:grid;grid-template-columns:18px minmax(0,1fr) auto;align-items:start;gap:10px}
        .akim-promise .promise-choice input{margin-top:4px;width:16px;height:16px}
        .akim-promise .promise-choice:has(input:disabled){cursor:default}
        .akim-promise .promise-price{white-space:nowrap;font-size:12px}
        .akim-promise .promise-field{display:grid;gap:5px;min-width:0;font-size:13px}
        .akim-promise select{width:100%;min-width:0;min-height:42px;padding:8px;border:1px solid var(--rule);border-radius:0;background:var(--white);color:var(--ink);font:inherit}
        .akim-promise select:disabled{color:var(--graph)}
        .akim-promise .promise-builder{display:grid;gap:10px;margin-top:12px}
        .akim-promise .promise-metrics{display:flex;flex-wrap:wrap;gap:16px 28px;margin-top:18px}
        .akim-promise .promise-decisions{list-style:none;margin:14px 0;padding:0}
        .akim-promise .promise-decision{display:flex;gap:10px;align-items:baseline;padding:9px 0;border-top:1px solid var(--rule);font-size:14px}
        .akim-promise .promise-decision .code{flex:none}
        .akim-promise .promise-alone{display:flex;justify-content:space-between;align-items:baseline;gap:12px;padding:9px 0;border-top:1px solid var(--rule);font-size:14px}
        .akim-promise .promise-zero{margin:0}
        .akim-promise .promise-note{font-size:13px;color:var(--graph);margin:8px 0 0}
        .akim-promise .promise-result{min-width:0}
        .akim-promise .promise-verdict{font-size:18px;line-height:1.4;margin:14px 0 0}
        .akim-promise .promise-hero{padding:20px 0 10px}
        .akim-promise .promise-custom-row{display:flex;align-items:center;gap:8px}
        .akim-promise .promise-custom-row label{flex:1;min-width:0}
        .akim-promise [hidden]{display:none!important}
        @media(min-width:1024px){.akim-promise .promise-layout{grid-template-columns:minmax(0,1fr) minmax(0,1.15fr)}}
      </style>
      <header class="hsec"><h2 class="h2">Цена обещания</h2><p>Сколько баллов Score стоит сдержать слово</p></header>
      ${ctx.mock ? '<p class="tape" style="margin:0 0 16px">Демо: записанный расчёт для плана из ТЗ и отмеченного обещания. Выбор обещаний и проверка другого плана доступны при подключении к серверу.</p>' : ''}
      <div data-content></div>`;
    el.replaceChildren(root);
    const content = root.querySelector('[data-content]');
    let results;
    const busy = (target, text) => {
      target.setAttribute('aria-busy', 'true');
      target.innerHTML = `<div class="plate promise-pad" role="status"><span class="spin" aria-hidden="true"></span> ${escape(text)}</div>`;
    };
    function error(target, err, retry) {
      target.setAttribute('aria-busy', 'false');
      const status = err?.status ?? err?.response?.status;
      const detail = err?.detail ?? err?.data?.detail ?? err?.response?.data?.detail ?? err?.message ?? 'Нет связи с сервером';
      const title = status === 422 ? 'План или обещание не прошли проверку'
        : status === 500 ? 'Движок не смог рассчитать обещания' : 'Не удалось загрузить расчёт';
      target.innerHTML = `<div class="notice"><div role="alert"><h3 class="red">${title}</h3><p class="promise-zero">${escape(typeof detail === 'string' ? detail : JSON.stringify(detail))}</p></div><button type="button" class="btn btn-line" style="margin-top:14px">Повторить</button></div>`;
      target.querySelector('button').onclick = retry;
    }
    const isCurrent = (id) => alive && sequence === id;
    const location = (name) => name ? 'в ' + (dataset.districts.find((d) => d.name === name)?.cases?.loc || name) : 'весь город';
    const measure = (id) => dataset.measures.find((m) => m.id === id);
    const alone = (row) => row.feasible_alone === false ? 'Невыполнимо'
      : row.price_alone == null ? 'Ожидает расчёта' : number(row.price_alone) + ' балла';

    function choice(row) {
      const key = keyOf(row);
      return `<label class="ch promise-choice" data-selected="${selected.has(key)}">
        <input type="checkbox" data-promise="${escape(key)}" ${selected.has(key) ? 'checked' : ''} ${ctx.mock ? 'disabled' : ''}>
        <span>${escape(row.label)}</span><span class="mono promise-price ${row.feasible_alone === false ? 'red' : 'mut'}">${escape(alone(row))}</span></label>`;
    }
    function wireChoices(target) {
      target.querySelectorAll('[data-promise]').forEach((input) => {
        input.onchange = () => {
          const key = input.dataset.promise;
          if (input.checked) selected.set(key, JSON.parse(key)); else selected.delete(key);
          input.closest('label').dataset.selected = String(input.checked);
          refresh();
        };
      });
    }
    function renderCustom() {
      const target = root.querySelector('[data-custom]');
      target.innerHTML = [...custom.values()].map((row) => `<div class="promise-custom-row">${choice(row)}<button type="button" class="x" data-remove="${escape(keyOf(row))}" aria-label="${escape('Удалить обещание: ' + row.label)}">×</button></div>`).join('');
      wireChoices(target);
      target.querySelectorAll('[data-remove]').forEach((button) => {
        button.onclick = () => {
          selected.delete(button.dataset.remove);
          custom.delete(button.dataset.remove);
          renderCustom();
          root.querySelector('[data-add]').focus();
          refresh();
        };
      });
    }
    function shell() {
      content.setAttribute('aria-busy', 'false');
      content.innerHTML = `<div class="promise-layout">
        <div class="promise-stack" style="align-content:start">
          <section class="sheet-w promise-pad"><h3 class="sm promise-zero">Что обещаем жителям</h3>
            <p class="promise-note" style="margin-bottom:12px">Справа — цена каждого обещания по отдельности, в баллах Score. Цены не складываются.</p>
            <div class="promise-choices" data-catalog>${catalog.map(choice).join('')}</div>
          </section>
          <section class="plate promise-pad"><h3 class="sm promise-zero">Своё обещание</h3>
            <form class="promise-builder">
              <label class="promise-field">Обещание<select name="kind" ${ctx.mock ? 'disabled' : ''}><option value="district_project">Проект в районе</option><option value="include">Включить меру в план</option></select></label>
              <label class="promise-field" data-measure-field hidden>Мера<select name="measure" ${ctx.mock ? 'disabled' : ''}>${dataset.measures.map((m) => `<option value="${escape(m.id)}">${escape(m.id + ' · ' + m.name)}</option>`).join('')}</select></label>
              <label class="promise-field" data-district-field>Район<select name="district" ${ctx.mock ? 'disabled' : ''}>${dataset.districts.map((d) => `<option value="${escape(d.name)}">${escape(d.name)}</option>`).join('')}</select></label>
              <p class="promise-note" data-city hidden>Городская мера действует во всём городе.</p>
              <button type="submit" class="btn btn-line" data-add ${ctx.mock ? 'disabled' : ''}>Добавить обещание</button>
            </form>
            <div class="promise-choices" data-custom style="margin-top:12px"></div>
          </section>
        </div>
        <div class="promise-result promise-stack" data-result aria-live="polite" aria-atomic="true" style="align-content:start"></div>
      </div>`;
      results = root.querySelector('[data-result]');
      wireChoices(root.querySelector('[data-catalog]'));
      const form = root.querySelector('form');
      const fields = form.elements;
      const adjust = () => {
        const include = fields.kind.value === 'include';
        const city = include && measure(fields.measure.value)?.type === 'city';
        root.querySelector('[data-measure-field]').hidden = !include;
        root.querySelector('[data-district-field]').hidden = city;
        root.querySelector('[data-city]').hidden = !city;
        fields.district.disabled = !!ctx.mock || city;
      };
      fields.kind.onchange = adjust;
      fields.measure.onchange = adjust;
      form.onsubmit = (event) => {
        event.preventDefault();
        if (ctx.mock) return;
        const m = measure(fields.measure.value), district = fields.district.value;
        const row = fields.kind.value === 'district_project'
          ? { type: 'district_project', district, label: 'Проект ' + location(district) }
          : { type: 'include', measure: m.id, district: m.type === 'city' ? null : district,
            label: m.name + ' · ' + location(m.type === 'city' ? null : district) };
        const key = keyOf(row);
        selected.set(key, constraint(row));
        const existing = [...root.querySelectorAll('[data-catalog] input')].find((input) => input.dataset.promise === key);
        if (existing) { existing.checked = true; existing.closest('label').dataset.selected = 'true'; }
        else custom.set(key, row);
        renderCustom();
        refresh();
      };
    }

    function render(data) {
      results.setAttribute('aria-busy', 'false');
      const best = data.best, own = data.your_plan;
      results.innerHTML = `<section class="paper strip">
        <h3 class="lbl promise-zero">${data.feasible ? 'Цена всех выбранных обещаний' : 'Обещания несовместимы'}</h3>
        ${data.feasible ? `<div class="promise-hero"><span class="big">${escape(number(data.price))}</span> <span class="sm">балла Score</span></div>` : ''}
        <p class="promise-verdict ${data.feasible ? '' : 'red'}">${escape(prose(data.verdict))}</p>
        <p class="promise-note">Допустимых планов: <span class="mono">${escape(integer(data.count_feasible))}</span> из <span class="mono">${escape(integer(data.total_valid))}</span>.</p>
      </section>
      ${best ? `<section class="sheet-w promise-pad"><h3 class="sm promise-zero">Лучший план, который держит слово</h3>
        <div class="promise-metrics"><div><div class="lbl">Score с обещаниями</div><div class="mid peni">${escape(number(best.score))}</div></div><div><div class="lbl">Без ограничений</div><div class="mid">${escape(number(data.unconstrained_best))}</div></div><div><div class="lbl">Бюджет, ед.</div><div class="mid">${escape(number(best.cost))}</div></div></div>
        <ul class="promise-decisions">${best.plan.decisions.map((d) => `<li class="promise-decision"><span class="code">${escape(d.measure)}</span><span>${escape(measure(d.measure)?.name || d.measure)}<span class="mut" style="display:block">${escape(location(d.district))}</span></span></li>`).join('')}</ul>
        <button type="button" class="btn btn-pen" data-apply ${typeof ctx.onApplyPlan === 'function' ? '' : 'disabled'}>Загрузить этот план</button>
        <p class="promise-note">Рейтинг акима: <span class="mono">${escape(number(best.approval))}%</span> — слой политического риска, не входит в Score.</p>
        <p data-apply-status role="status" class="promise-note"></p>
      </section>` : ''}
      <section class="notice"><h3>${ctx.mock ? 'План из примера ТЗ' : 'Текущий план'}</h3>
        ${own ? `<p class="promise-zero ${own.keeps_promises ? 'peni' : 'red'}"><b>${own.keeps_promises ? 'Все выбранные обещания выполнены' : 'Есть нарушенные обещания'}</b> · Score <span class="mono">${escape(number(own.score))}</span></p>${own.broken.length ? `<ul class="red" style="margin-top:10px">${own.broken.map((label) => `<li>${escape(label)}</li>`).join('')}</ul>` : ''}` : '<p class="promise-zero mut">Выберите текущий план в кабинете, чтобы проверить обещания.</p>'}
      </section>
      ${data.promises.length ? `<section class="sheet-w promise-pad"><h3 class="sm" style="margin:0 0 12px">Цена каждого обещания отдельно</h3>${data.promises.map((row) => `<div class="promise-alone"><span>${escape(row.label)}</span><b class="mono promise-price ${row.feasible_alone ? 'peni' : 'red'}">${escape(alone(row))}</b></div>`).join('')}<p class="promise-note">Это отдельные расчёты. Общая цена показана выше.</p></section>` : ''}`;
      const apply = results.querySelector('[data-apply]');
      if (apply) apply.onclick = async () => {
        apply.disabled = true;
        const status = results.querySelector('[data-apply-status]');
        try {
          await ctx.onApplyPlan(clone(best.plan));
          if (!alive) return;
          status.textContent = 'План загружен в кабинет.';
          if (!ctx.mock) { ctx = { ...ctx, plan: clone(best.plan) }; await refresh(); }
        } catch (err) {
          status.classList.add('red');
          status.textContent = 'Не удалось загрузить план: ' + (err?.message || 'Повторите попытку.');
        } finally { if (alive) apply.disabled = false; }
      };
    }

    async function refresh() {
      const id = ++sequence;
      busy(results, 'Ищем лучший план с выбранными обещаниями…');
      try {
        const data = ctx.mock ? saved.price : await ctx.api('/api/promise', { promises: [...selected.values()].map(constraint), plan: ctx.plan ?? null });
        if (!isCurrent(id)) return;
        for (const row of data.promises) if (custom.has(keyOf(row))) custom.set(keyOf(row), row);
        renderCustom();
        render(data);
      } catch (err) { if (isCurrent(id)) error(results, err, refresh); }
    }

    async function start() {
      const id = ++sequence;
      busy(content, 'Загружаем каталог обещаний…');
      try {
        const loaded = await Promise.all(ctx.mock
          ? [fixture('promise'), fixture('dataset')]
          : [ctx.api('/api/promise/catalog'), ctx.api('/api/dataset')]);
        if (!isCurrent(id)) return;
        dataset = loaded[1];
        if (ctx.mock) { saved = loaded[0]; catalog = saved.catalog; }
        else catalog = loaded[0];
        const initial = ctx.mock ? saved.price.promises : catalog.filter((row) => row.type === 'min_approval');
        selected = new Map(initial.map((row) => [keyOf(row), constraint(row)]));
        custom = new Map(initial.filter((row) => !catalog.some((entry) => keyOf(entry) === keyOf(row))).map((row) => [keyOf(row), row]));
        shell();
        await refresh();
      } catch (err) { if (isCurrent(id)) error(content, err, start); }
    }

    // Optional lifecycle hooks: update when the cabinet plan changes, destroy
    // on unmount. Re-mounting the same container also invalidates old requests.
    const controller = {
      update(plan) { ctx = { ...ctx, plan }; return results && alive ? refresh() : Promise.resolve(); },
      destroy() { alive = false; sequence++; root.remove(); if (mounts.get(el) === controller) mounts.delete(el); },
    };
    mounts.set(el, controller);
    controller.ready = start();
    return controller;
  }

  window.AkimPanels = window.AkimPanels || {};
  window.AkimPanels.promise = { mount };
}());
