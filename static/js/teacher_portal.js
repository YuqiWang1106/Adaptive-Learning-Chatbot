(() => {
  const normalize = (value) => String(value || '').trim().toLowerCase();

  function bindDialogs() {
    document.querySelectorAll('[data-dialog-open]').forEach((trigger) => {
      trigger.addEventListener('click', () => {
        const dialog = document.getElementById(trigger.dataset.dialogOpen);
        if (dialog && typeof dialog.showModal === 'function') dialog.showModal();
      });
    });

    document.querySelectorAll('[data-dialog-close]').forEach((trigger) => {
      trigger.addEventListener('click', () => trigger.closest('dialog')?.close());
    });

    document.querySelectorAll('dialog.tp-dialog').forEach((dialog) => {
      dialog.addEventListener('click', (event) => {
        if (event.target === dialog) dialog.close();
      });
    });
  }

  function activateTab(scope, targetId, updateHash = false) {
    const target = scope.querySelector(`#${CSS.escape(targetId)}`);
    if (!target) return;
    scope.querySelectorAll('[data-tab-target]').forEach((button) => {
      const active = button.dataset.tabTarget === targetId;
      button.classList.toggle('is-active', active);
      button.setAttribute('aria-selected', active ? 'true' : 'false');
      button.tabIndex = active ? 0 : -1;
    });
    scope.querySelectorAll('[data-tab-panel]').forEach((panel) => {
      panel.hidden = panel.id !== targetId;
    });
    if (updateHash) history.replaceState(null, '', `#${targetId}`);
  }

  function bindTabs() {
    document.querySelectorAll('[data-tab-scope]').forEach((scope) => {
      scope.querySelectorAll('[data-tab-target]').forEach((button) => {
        button.addEventListener('click', () => activateTab(scope, button.dataset.tabTarget, true));
        button.addEventListener('keydown', (event) => {
          if (!['ArrowLeft', 'ArrowRight'].includes(event.key)) return;
          const tabs = Array.from(scope.querySelectorAll('[data-tab-target]'));
          const current = tabs.indexOf(button);
          const offset = event.key === 'ArrowRight' ? 1 : -1;
          const next = tabs[(current + offset + tabs.length) % tabs.length];
          next.focus();
          activateTab(scope, next.dataset.tabTarget, true);
        });
      });

      const hashTarget = window.location.hash.slice(1);
      if (hashTarget && scope.querySelector(`#${CSS.escape(hashTarget)}`)) {
        activateTab(scope, hashTarget);
      }
    });

    document.querySelectorAll('[data-tab-jump]').forEach((trigger) => {
      trigger.addEventListener('click', () => {
        const scope = trigger.closest('[data-tab-scope]');
        if (scope) activateTab(scope, trigger.dataset.tabJump, true);
      });
    });
  }

  function bindSearch(inputSelector, itemSelector, emptySelector) {
    document.querySelectorAll(inputSelector).forEach((input) => {
      const root = input.closest('.tp-shell') || document;
      const items = Array.from(root.querySelectorAll(itemSelector));
      const empty = root.querySelector(emptySelector);
      const update = () => {
        const query = normalize(input.value);
        let visible = 0;
        items.forEach((item) => {
          const matches = !query || normalize(item.dataset.searchText || item.textContent).includes(query);
          item.hidden = !matches;
          if (matches) visible += 1;
        });
        if (empty) empty.hidden = visible !== 0;
      };
      input.addEventListener('input', update);
    });
  }

  function bindEvidenceFilters() {
    document.querySelectorAll('[data-evidence-filter]').forEach((button) => {
      button.addEventListener('click', () => {
        const panel = button.closest('[data-tab-panel]') || document;
        const filter = button.dataset.evidenceFilter;
        let visible = 0;
        panel.querySelectorAll('[data-evidence-filter]').forEach((item) => item.classList.toggle('is-active', item === button));
        panel.querySelectorAll('[data-evidence-item]').forEach((item) => {
          const matches = filter === 'all' || item.dataset.evidenceItem === filter;
          item.hidden = !matches;
          if (matches) visible += 1;
        });
        const empty = panel.querySelector('[data-evidence-empty]');
        if (empty) empty.hidden = visible !== 0;
      });
    });
  }

  function prepareCanvas(canvas) {
    const bounds = canvas.getBoundingClientRect();
    const width = Math.max(280, Math.round(bounds.width));
    const height = Math.max(220, Math.round(bounds.height));
    const ratio = Math.max(1, window.devicePixelRatio || 1);
    canvas.width = Math.round(width * ratio);
    canvas.height = Math.round(height * ratio);
    const context = canvas.getContext('2d');
    context.setTransform(ratio, 0, 0, ratio, 0, 0);
    context.clearRect(0, 0, width, height);
    context.lineJoin = 'round';
    context.lineCap = 'round';
    return { context, width, height };
  }

  function renderGoalRadar() {
    const source = document.getElementById('tp-goal-dimension-data');
    const canvas = document.getElementById('tp-goal-radar');
    if (!source || !canvas) return;
    const values = JSON.parse(source.textContent || '{}');
    const dimensions = [
      { key: 'facts', label: 'Facts', color: '#2678b8' },
      { key: 'procedures', label: 'Procedures', color: '#d57a22' },
      { key: 'strategies', label: 'Strategies', color: '#238b55' },
      { key: 'rationales', label: 'Rationales', color: '#a94f9a' },
    ];

    const draw = () => {
      const { context, width, height } = prepareCanvas(canvas);
      const centerX = width / 2;
      const centerY = height / 2 + 2;
      const radius = Math.min(width * .27, height * .31);
      const pointAt = (index, scale = 1) => {
        const angle = (-Math.PI / 2) + (index * Math.PI * 2 / dimensions.length);
        return { x: centerX + Math.cos(angle) * radius * scale, y: centerY + Math.sin(angle) * radius * scale };
      };

      context.strokeStyle = 'rgba(91, 98, 108, 0.15)';
      context.lineWidth = 1;
      [.25, .5, .75, 1].forEach((scale) => {
        context.beginPath();
        dimensions.forEach((_, index) => {
          const point = pointAt(index, scale);
          if (index === 0) context.moveTo(point.x, point.y);
          else context.lineTo(point.x, point.y);
        });
        context.closePath();
        context.stroke();
      });
      dimensions.forEach((_, index) => {
        const point = pointAt(index);
        context.beginPath();
        context.moveTo(centerX, centerY);
        context.lineTo(point.x, point.y);
        context.stroke();
      });

      const masteryPoints = dimensions.map((dimension, index) => pointAt(index, (values[dimension.key] || 0) / 100));
      context.beginPath();
      masteryPoints.forEach((point, index) => {
        if (index === 0) context.moveTo(point.x, point.y);
        else context.lineTo(point.x, point.y);
      });
      context.closePath();
      context.fillStyle = 'rgba(31, 155, 180, 0.16)';
      context.fill();
      context.strokeStyle = '#1f9bb4';
      context.lineWidth = 2.5;
      context.stroke();

      context.font = '700 12px Manrope, sans-serif';
      dimensions.forEach((dimension, index) => {
        const masteryPoint = masteryPoints[index];
        context.beginPath();
        context.arc(masteryPoint.x, masteryPoint.y, 4.5, 0, Math.PI * 2);
        context.fillStyle = dimension.color;
        context.fill();
        context.strokeStyle = '#fff';
        context.lineWidth = 2;
        context.stroke();

        const labelPoint = pointAt(index, 1.27);
        context.fillStyle = '#4f5965';
        context.textAlign = Math.abs(labelPoint.x - centerX) < 8 ? 'center' : labelPoint.x < centerX ? 'right' : 'left';
        context.textBaseline = labelPoint.y < centerY ? 'bottom' : labelPoint.y > centerY ? 'top' : 'middle';
        context.fillText(`${dimension.label} ${values[dimension.key] || 0}%`, labelPoint.x, labelPoint.y);
      });
      canvas.dataset.rendered = 'true';
    };
    draw();
    window.addEventListener('resize', draw, { passive: true });
  }

  function renderGoalTrend() {
    const source = document.getElementById('tp-goal-trend-data');
    const canvas = document.getElementById('tp-goal-trend');
    if (!source || !canvas) return;
    const allPoints = JSON.parse(source.textContent || '[]');
    if (allPoints.length < 2) return;
    const colors = {
      facts: '#2678b8',
      procedures: '#d57a22',
      strategies: '#238b55',
      rationales: '#a94f9a',
      overall: '#19212b',
    };
    const labels = {
      facts: 'Facts',
      procedures: 'Procedures',
      strategies: 'Strategies',
      rationales: 'Rationales',
      overall: 'Overall',
    };
    let activePoints = allPoints;
    const draw = () => {
      const { context, width, height } = prepareCanvas(canvas);
      const compact = width < 560;
      const margin = { top: 22, right: 18, bottom: compact ? 82 : 62, left: 46 };
      const plotWidth = width - margin.left - margin.right;
      const plotHeight = height - margin.top - margin.bottom;
      const xFor = (index) => margin.left + (index * plotWidth / Math.max(1, activePoints.length - 1));
      const yFor = (value) => margin.top + ((100 - value) * plotHeight / 100);

      context.font = '500 11px Manrope, sans-serif';
      context.textAlign = 'right';
      context.textBaseline = 'middle';
      for (let value = 0; value <= 100; value += 20) {
        const y = yFor(value);
        context.strokeStyle = 'rgba(91, 98, 108, 0.12)';
        context.lineWidth = 1;
        context.beginPath();
        context.moveTo(margin.left, y);
        context.lineTo(width - margin.right, y);
        context.stroke();
        context.fillStyle = '#6d7681';
        context.fillText(`${value}%`, margin.left - 8, y);
      }

      context.textAlign = 'center';
      context.textBaseline = 'top';
      const labelStep = activePoints.length > 8 ? 2 : 1;
      activePoints.forEach((point, index) => {
        if (index % labelStep !== 0 && index !== activePoints.length - 1) return;
        context.fillStyle = '#6d7681';
        context.fillText(point.label, xFor(index), height - margin.bottom + 10);
      });

      Object.keys(labels).forEach((key) => {
        context.beginPath();
        activePoints.forEach((point, index) => {
          const x = xFor(index);
          const y = yFor(point[key] || 0);
          if (index === 0) context.moveTo(x, y);
          else context.lineTo(x, y);
        });
        context.strokeStyle = colors[key];
        context.lineWidth = key === 'overall' ? 3 : 2;
        context.setLineDash(key === 'overall' ? [6, 4] : []);
        context.stroke();
        context.setLineDash([]);
        activePoints.forEach((point, index) => {
          context.beginPath();
          context.arc(xFor(index), yFor(point[key] || 0), key === 'overall' ? 3.2 : 2.2, 0, Math.PI * 2);
          context.fillStyle = colors[key];
          context.fill();
        });
      });

      const legendY = height - (compact ? 42 : 22);
      const itemWidth = compact ? Math.floor((width - 24) / 3) : Math.floor((width - 24) / 5);
      context.font = '700 10.5px Manrope, sans-serif';
      context.textAlign = 'left';
      context.textBaseline = 'middle';
      Object.keys(labels).forEach((key, index) => {
        const row = compact && index >= 3 ? 1 : 0;
        const column = compact && index >= 3 ? index - 3 : index;
        const x = 14 + column * itemWidth;
        const y = legendY + row * 22 - (compact ? 11 : 0);
        context.beginPath();
        context.arc(x + 4, y, 4, 0, Math.PI * 2);
        context.fillStyle = colors[key];
        context.fill();
        context.fillStyle = '#4f5965';
        context.fillText(labels[key], x + 13, y);
      });
      canvas.dataset.rendered = 'true';
      canvas.dataset.pointCount = String(activePoints.length);
    };

    const update = (range) => {
      const count = range === 'all' ? allPoints.length : Number(range);
      activePoints = allPoints.slice(-count);
      draw();
    };

    document.querySelectorAll('[data-trend-range]').forEach((button) => {
      button.addEventListener('click', () => {
        document.querySelectorAll('[data-trend-range]').forEach((item) => item.classList.toggle('is-active', item === button));
        update(button.dataset.trendRange);
      });
    });
    update('all');
    window.addEventListener('resize', draw, { passive: true });
  }

  function bindPrint() {
    document.querySelectorAll('[data-print-page]').forEach((button) => button.addEventListener('click', () => window.print()));
  }

  function closeOpenMenus(event) {
    document.querySelectorAll('.tp-account-menu[open], .tp-action-menu[open], .tp-row-actions details[open]').forEach((details) => {
      if (!details.contains(event.target)) details.removeAttribute('open');
    });
  }

  window.addEventListener('DOMContentLoaded', () => {
    bindDialogs();
    bindTabs();
    bindSearch('[data-class-search]', '[data-class-card]', '[data-class-empty]');
    bindSearch('[data-goal-search]', '[data-goal-nav-item]', '[data-goal-empty]');
    bindEvidenceFilters();
    bindPrint();
    renderGoalRadar();
    renderGoalTrend();
    document.addEventListener('click', closeOpenMenus);
  });
})();
