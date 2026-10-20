'use strict';
(() => {
  const root = document.querySelector('[data-evaluation]');
  if (!root) return;
  const csrf = document.querySelector('meta[name="csrf-token"]').content;
  const cards = [...document.querySelectorAll('[data-question]')];
  const inputs = [...document.querySelectorAll('[data-answer]')];
  const nav = [...document.querySelectorAll('[data-go]')];
  const status = document.getElementById('save-status');
  const alert = document.getElementById('network-alert');
  const dialog = document.getElementById('submit-dialog');
  const storageKey = `sql-assessment-${root.dataset.evaluation}`;
  let answers = {}, revision = 0, current = 0, dirty = false;
  let ready = false, locked = false, submitting = false, saving = null, debounce;
  let deadline = 0, serverTime = 0, referenceTime = 0, requestInFlight = false;

  function storageRead(key) { try { return sessionStorage.getItem(key); } catch { return null; } }
  function storageWrite(key, value) { try { sessionStorage.setItem(key, value); } catch { /* Server saving still works. */ } }
  function clearDraft() { try { sessionStorage.removeItem(storageKey); } catch { /* Optional cache. */ } }
  function cacheDraft() { storageWrite(storageKey, JSON.stringify({revision, answers})); }
  function setStatus(text, state = '') { status.textContent = text; status.className = `save-status ${state}`; }
  function message(text) { alert.textContent = text; alert.hidden = !text; }
  function disableAnswers(value) { inputs.forEach(input => { input.disabled = value; }); }
  disableAnswers(true);

  async function api(path, payload) {
    const response = await fetch(path, {
      method: payload ? 'POST' : 'GET', credentials: 'same-origin', cache: 'no-store',
      headers: {'Content-Type': 'application/json', 'X-CSRF-Token': csrf},
      body: payload ? JSON.stringify(payload) : undefined,
      signal: AbortSignal.timeout(10000)
    });
    let data;
    try { data = await response.json(); }
    catch { throw new Error('No se pudo verificar la sesión. Conserva la página abierta e intenta recargar.'); }
    if (!response.ok) {
      if (['submitted', 'expired', 'graded'].includes(data.status)) return finishScreen();
      if (data.status === 'conflict') {
        locked = true;
        disableAnswers(true);
        document.getElementById('confirm-submit').disabled = true;
        message(data.error + ' Tus cambios locales permanecen en esta pestaña.');
      }
      throw new Error(data.error || 'No se pudo completar la solicitud.');
    }
    return data;
  }

  function finishScreen() {
    locked = true;
    dirty = false;
    disableAnswers(true);
    clearDraft();
    window.location.replace('/gracias');
    throw new Error('La evaluación ha finalizado.');
  }

  function syncClock(data) {
    deadline = data.deadline;
    serverTime = data.server_now;
    referenceTime = performance.now();
    tick();
  }

  function renderProgress() {
    const count = Object.values(answers).filter(value => value.trim()).length;
    document.getElementById('progress').value = count;
    document.getElementById('progress-label').textContent = `${count} de ${cards.length}`;
    document.getElementById('answered-count').textContent = count;
    cards.forEach((card, index) => {
      const answered = Boolean((answers[card.dataset.question] || '').trim());
      nav[index].classList.toggle('answered', answered);
      const mark = nav[index].querySelector('.answer-mark');
      mark.textContent = answered ? '✓' : '○';
      mark.setAttribute('aria-label', answered ? 'Con respuesta' : 'Sin respuesta');
      const counter = card.querySelector('[data-count]');
      if (counter) counter.textContent = `${(answers[card.dataset.question] || '').length.toLocaleString('es-MX')} / 12,000`;
    });
  }

  function displayAnswers() {
    inputs.forEach(input => {
      const value = answers[input.dataset.answer] || '';
      if (input.type === 'radio') input.checked = input.value === value;
      else input.value = value;
    });
    renderProgress();
  }

  function navigate(index, focus = true) {
    current = Math.max(0, Math.min(cards.length - 1, index));
    cards.forEach((card, i) => { card.hidden = i !== current; });
    nav.forEach((button, i) => {
      button.classList.toggle('active', i === current);
      if (i === current) button.setAttribute('aria-current', 'step');
      else button.removeAttribute('aria-current');
    });
    document.getElementById('previous').disabled = current === 0;
    document.getElementById('next').hidden = current === cards.length - 1;
    document.getElementById('review-submit').hidden = current !== cards.length - 1;
    document.getElementById('current-section').textContent = cards[current].dataset.section.toUpperCase();
    storageWrite(storageKey + '-question', String(current));
    if (focus) cards[current].querySelector('h1').focus({preventScroll: true});
    if (dirty) void save();
  }

  async function save() {
    if (!ready || locked) return;
    if (saving) return saving;
    saving = (async () => {
      while (dirty && !locked) {
        const snapshot = {...answers};
        dirty = false;
        setStatus('Guardando respuestas…', 'pending');
        try {
          const data = await api('/api/respuestas', {revision, answers: snapshot});
          revision = data.revision;
          syncClock(data);
          if (dirty) cacheDraft();
          else clearDraft();
          setStatus('✓ Todos los cambios guardados');
          message('');
        } catch (error) {
          if (!locked) { dirty = true; cacheDraft(); }
          setStatus('Cambios pendientes de guardar', 'error');
          message(error.message + (locked ? '' : ' Se reintentará automáticamente. No cierres esta pestaña.'));
          break;
        }
      }
    })();
    try { await saving; } finally { saving = null; }
  }

  function edited(input) {
    if (!ready || locked || submitting) return;
    answers[input.dataset.answer] = input.value;
    dirty = true;
    cacheDraft();
    renderProgress();
    setStatus('Cambios pendientes…', 'pending');
    clearTimeout(debounce);
    debounce = setTimeout(() => { void save(); }, 500);
  }

  inputs.forEach(input => {
    input.addEventListener(input.type === 'radio' ? 'change' : 'input', () => edited(input));
    if (input.closest('.sql-editor')) input.addEventListener('keydown', event => {
      if (event.key === 'Tab' && !event.shiftKey && !input.disabled) {
        event.preventDefault();
        input.setRangeText('    ', input.selectionStart, input.selectionEnd, 'end');
        edited(input);
      }
    });
  });
  nav.forEach(button => button.addEventListener('click', () => navigate(Number(button.dataset.go))));
  document.getElementById('previous').addEventListener('click', () => navigate(current - 1));
  document.getElementById('next').addEventListener('click', () => navigate(current + 1));

  function openSubmit() {
    if (!ready || locked) return;
    renderProgress();
    document.getElementById('submit-error').hidden = true;
    dialog.showModal();
  }
  document.getElementById('review-submit').addEventListener('click', openSubmit);
  document.getElementById('open-submit').addEventListener('click', openSubmit);
  document.getElementById('cancel-submit').addEventListener('click', () => { if (!submitting) dialog.close(); });
  dialog.addEventListener('cancel', event => { if (submitting) event.preventDefault(); });
  document.getElementById('confirm-submit').addEventListener('click', async () => {
    if (submitting || locked) return;
    submitting = true;
    disableAnswers(true);
    const button = document.getElementById('confirm-submit');
    button.disabled = true;
    button.textContent = 'Entregando…';
    try {
      await save();
      if (dirty || locked) throw new Error('Aún hay cambios sin guardar. Revisa la conexión antes de entregar.');
      await api('/api/entregar', {revision, answers: {...answers}});
      clearDraft();
      dirty = false;
      locked = true;
      window.location.replace('/gracias');
    } catch (error) {
      const errorBox = document.getElementById('submit-error');
      errorBox.textContent = error.message;
      errorBox.hidden = false;
      button.textContent = 'Reintentar entrega';
      button.disabled = locked;
      if (!locked) disableAnswers(false);
      submitting = false;
    }
  });

  function tick() {
    if (!ready || !deadline || locked) return;
    const remaining = Math.max(0, Math.ceil(deadline - serverTime - (performance.now() - referenceTime) / 1000));
    document.getElementById('timer').textContent = `${String(Math.floor(remaining / 60)).padStart(2, '0')}:${String(remaining % 60).padStart(2, '0')}`;
    document.querySelector('.timer').classList.toggle('warning', remaining <= 300);
    if (remaining === 0) {
      disableAnswers(true);
      document.getElementById('confirm-submit').disabled = true;
      setStatus('Tiempo finalizado. Verificando entrega automática…', 'pending');
      void poll();
    }
  }

  async function initialize() {
    try {
      const data = await api('/api/evaluacion');
      if (data.status !== 'in_progress') return finishScreen();
      answers = data.answers;
      revision = data.revision;
      let draft;
      try { draft = JSON.parse(storageRead(storageKey)); } catch { /* Ignore damaged optional draft. */ }
      if (draft && draft.revision === revision && draft.answers && typeof draft.answers === 'object') {
        answers = {...answers, ...draft.answers};
        dirty = true;
      } else if (draft) {
        message('Se recuperaron las respuestas del servidor. Un borrador antiguo de esta pestaña no se aplicó porque existe una versión más reciente.');
      }
      displayAnswers();
      ready = true;
      disableAnswers(false);
      syncClock(data);
      navigate(Number(storageRead(storageKey + '-question')) || 0, false);
      setStatus(dirty ? 'Recuperando cambios locales…' : '✓ Todos los cambios guardados', dirty ? 'pending' : '');
      if (dirty) void save();
    } catch (error) {
      if (!locked) { message(error.message + ' Reintentando conexión…'); setStatus('Sin conexión con el servidor', 'error'); }
    }
  }

  async function poll() {
    if (requestInFlight || locked || saving || submitting) return;
    requestInFlight = true;
    try {
      if (!ready) { await initialize(); return; }
      const data = await api('/api/evaluacion');
      if (data.status !== 'in_progress') return finishScreen();
      if (data.revision !== revision) {
        // A timed-out save may have committed: recover only if the contents agree.
        if (JSON.stringify(data.answers) === JSON.stringify(answers)) {
          revision = data.revision; dirty = false; clearDraft();
          setStatus('✓ Todos los cambios guardados'); message('');
        } else {
          locked = true; disableAnswers(true);
          message('La versión del servidor cambió. Recarga para continuar con las respuestas guardadas; evita usar varias pestañas.');
          return;
        }
      }
      syncClock(data);
      if (dirty) void save();
    } catch (error) {
      if (!locked) { message('No hay conexión con el servidor. El tiempo continúa; conserva esta pestaña abierta.'); }
    } finally { requestInFlight = false; }
  }

  window.addEventListener('beforeunload', event => {
    if (dirty && !locked) { event.preventDefault(); event.returnValue = ''; }
  });
  window.addEventListener('online', () => { void poll(); });
  document.addEventListener('visibilitychange', () => { if (!document.hidden) void poll(); else if (dirty) void save(); });
  setInterval(tick, 250);
  setInterval(() => { void poll(); }, 5000);
  void initialize();
})();
