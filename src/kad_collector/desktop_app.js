const token = document.querySelector('meta[name="kad-desktop-token"]').content;

const emptyFilters = () => ({
  source_files: [], concursos: [], boards: [], years: [], roles: [], variants: [], levels: [],
  disciplines: [], subjects: [], topics: [], difficulties: [], statuses: [],
  quality_flags: [], search: '', min_confidence: null,
});

const state = {
  bootstrap: null,
  query: null,
  filters: emptyFilters(),
  selectedPaths: [],
  selectedQuestionIds: new Set(),
  currentQuestion: null,
  currentAudit: [],
  polling: null,
  activeSection: 'workspace',
  selectedSourceId: null,
};

const byId = (id) => document.getElementById(id);
const optional = (id) => byId(id).value.trim() || null;
const numberOrNull = (id) => {
  const value = byId(id).value.trim();
  return value ? Number(value) : null;
};

async function request(path, options = {}) {
  const response = await fetch(path, {
    ...options,
    headers: {
      'Content-Type': 'application/json',
      'X-KAD-Desktop-Token': token,
      ...(options.headers || {}),
    },
  });
  const payload = await response.json();
  if (!response.ok) throw new Error(payload.error || 'Falha na operação local.');
  return payload;
}

async function openAuthenticatedPdf(event) {
  event.preventDefault();
  const popup = window.open('about:blank', '_blank');
  if (!popup) {
    toast('Permita a abertura da janela para visualizar o PDF.', 'error');
    return;
  }
  popup.opener = null;
  try {
    const response = await fetch(event.currentTarget.href, {
      headers: {'X-KAD-Desktop-Token': token},
    });
    if (!response.ok) {
      const payload = await response.json().catch(() => ({}));
      throw new Error(payload.error || 'Falha ao abrir o PDF local.');
    }
    const objectUrl = URL.createObjectURL(await response.blob());
    const page = Number(event.currentTarget.dataset.page || 0);
    popup.location.replace(page > 0 ? `${objectUrl}#page=${page}` : objectUrl);
    window.setTimeout(() => URL.revokeObjectURL(objectUrl), 300000);
  } catch (error) {
    popup.close();
    toast(error.message, 'error');
  }
}

function toast(message, kind = 'info') {
  const element = byId('toast');
  element.textContent = message;
  element.className = `toast ${kind}`;
  element.hidden = false;
  clearTimeout(toast.timer);
  toast.timer = setTimeout(() => { element.hidden = true; }, 6500);
}

function statusLabel(status) {
  return {
    pending: 'Pendente', approved: 'Aprovada', rejected: 'Rejeitada',
    exception: 'Exceção', exported: 'Exportada', exportable: 'Exportável',
  }[status] || status;
}

function flagLabel(flag) {
  return {
    incomplete: 'Incompleta', without_explanation: 'Sem explicação', annulled: 'Anulada',
    without_answer: 'Sem gabarito', visual: 'Visual', missing_fields: 'Campos ausentes',
    low_confidence: 'Baixa confiança', duplicate: 'Duplicata',
  }[flag] || flag;
}

const facetDefinitions = [
  ['Origem', [
    ['source_files', 'PDF de origem'], ['concursos', 'Concurso'], ['boards', 'Banca'],
    ['years', 'Ano'], ['roles', 'Cargo'], ['variants', 'Variante'], ['levels', 'Nível'],
  ]],
  ['Conteúdo', [
    ['disciplines', 'Disciplina'], ['subjects', 'Assunto'], ['topics', 'Tópico'],
    ['difficulties', 'Dificuldade'],
  ]],
  ['Qualidade', [['quality_flags', 'Sinais de qualidade']]],
  ['Situação', [['statuses', 'Fluxo editorial']]],
];

function selectedValuesCount() {
  return Object.entries(state.filters)
    .filter(([key]) => !['search', 'min_confidence'].includes(key))
    .reduce((total, [, values]) => total + values.length, 0);
}

async function loadBootstrap({preserveQuery = false} = {}) {
  const payload = await request('/api/bootstrap');
  state.bootstrap = payload;
  if (!preserveQuery || !state.query) state.query = payload;
  render();
  if (selectedValuesCount() || state.filters.search || state.filters.min_confidence !== null) {
    await runQuery();
  }
  schedulePoll();
}

async function runQuery() {
  state.query = await request('/api/query', {
    method: 'POST', body: JSON.stringify({filters: state.filters}),
  });
  renderQuery();
}

function render() {
  renderMetrics();
  renderJobs();
  renderSavedFilters();
  renderQuery();
  renderSourceCatalog();
  renderCollections();
  renderSection();
  const openaiOption = byId('import-classifier').querySelector('option[value="openai"]');
  const sourceOpenaiOption = byId('source-classifier').querySelector('option[value="openai"]');
  const configured = state.bootstrap.config.openaiConfigured;
  openaiOption.disabled = !configured;
  openaiOption.textContent = configured ? 'OpenAI configurada' : 'OpenAI não configurada';
  sourceOpenaiOption.disabled = !configured;
  sourceOpenaiOption.textContent = configured ? 'OpenAI configurada' : 'OpenAI não configurada';
}

function renderSection() {
  const collecting = state.activeSection === 'sources';
  byId('editorial-view').hidden = collecting;
  byId('source-view').hidden = !collecting;
}

function sourceById(sourceId) {
  return (state.bootstrap.sources || []).find((source) => source.id === sourceId) || null;
}

function selectSource(sourceId, {resetUrl = true} = {}) {
  const source = sourceById(sourceId);
  if (!source) return;
  state.selectedSourceId = source.id;
  byId('source-select').value = source.id;
  if (resetUrl) {
    byId('source-url').value = source.defaultUrl;
    byId('source-browser-enabled').checked = Boolean(source.engine?.browserAvailable);
    byId('source-robots-policy').value = source.engine?.robotsPolicy || 'enforce';
    byId('source-crawl-delay-policy').value = source.engine?.crawlDelayPolicy || 'enforce';
  }
  const details = byId('source-details');
  details.replaceChildren();
  const category = document.createElement('span');
  category.className = 'source-category';
  category.textContent = source.category;
  const copy = document.createElement('p');
  copy.textContent = source.description;
  const hosts = document.createElement('small');
  hosts.textContent = `Domínios permitidos: ${source.allowedHosts.join(', ')}`;
  details.append(category, copy, hosts);
  const strategy = document.createElement('small');
  strategy.textContent = `Descoberta: ${(source.engine?.strategies || ['html']).join(' → ')}`;
  details.append(strategy);
  if (source.notice) {
    const notice = document.createElement('small');
    notice.className = 'source-notice';
    notice.textContent = source.notice;
    details.append(notice);
  }
  if (source.urlHint) {
    const hint = document.createElement('small');
    hint.className = 'source-notice';
    hint.textContent = source.urlHint;
    details.append(hint);
  }
  byId('source-submit').disabled = !source.collectable;
  byId('source-submit').textContent = source.collectable
    ? 'Coletar deste link'
    : 'Somente referências';
  document.querySelectorAll('.source-card').forEach((card) => {
    card.classList.toggle('selected', card.dataset.sourceId === source.id);
  });
}

function renderSourceCatalog() {
  const sources = state.bootstrap.sources || [];
  const select = byId('source-select');
  const knownIds = Array.from(select.options).map((option) => option.value).join('|');
  const currentIds = sources.map((source) => source.id).join('|');
  if (knownIds !== currentIds) {
    select.replaceChildren();
    sources.forEach((source) => {
      const option = document.createElement('option');
      option.value = source.id;
      option.textContent = `${source.name}${source.collectable ? '' : ' — referências'}`;
      option.disabled = !source.collectable;
      select.append(option);
    });
  }
  if (!state.selectedSourceId || !sourceById(state.selectedSourceId)) {
    state.selectedSourceId = (sourceById('fgv_conhecimento') || sources.find((item) => item.collectable))?.id || null;
    if (state.selectedSourceId) selectSource(state.selectedSourceId, {resetUrl: true});
  } else {
    selectSource(state.selectedSourceId, {resetUrl: false});
  }

  byId('source-count').textContent = `${sources.filter((source) => source.collectable).length} fontes prontas`;
  const cache = state.bootstrap.collectionEngine?.cache || {};
  byId('engine-cache-summary').textContent = `CACHE ${formatBytes(cache.bytes || 0)} · ${cache.entries || 0} item(ns)`;
  const grid = byId('source-grid');
  grid.replaceChildren();
  sources.forEach((source) => {
    const card = document.createElement('article');
    card.className = `source-card${source.id === state.selectedSourceId ? ' selected' : ''}`;
    card.dataset.sourceId = source.id;
    const top = document.createElement('div');
    const category = document.createElement('span');
    category.className = 'source-category';
    category.textContent = source.category;
    const mode = document.createElement('span');
    mode.className = `source-mode ${source.collectable ? 'ready' : 'reference'}`;
    mode.textContent = source.collectable ? 'PRONTA' : 'REFERÊNCIA';
    top.append(category, mode);
    const title = document.createElement('h3');
    title.textContent = source.name;
    const copy = document.createElement('p');
    copy.textContent = source.description;
    const action = document.createElement('button');
    action.className = 'text-button source-card-action';
    action.type = 'button';
    action.disabled = !source.collectable;
    action.textContent = source.collectable ? 'Usar esta fonte →' : 'Download automático indisponível';
    action.addEventListener('click', () => selectSource(source.id));
    card.append(top, title, copy, action);
    grid.append(card);
  });
}

function collectionStatusLabel(status) {
  return {
    queued: 'Na fila', running: 'Baixando', processing: 'Processando',
    pausing: 'Pausando', paused: 'Pausada', cancelling: 'Cancelando',
    cancelled: 'Cancelada', completed: 'Concluída', needs_attention: 'Requer atenção',
    failed: 'Falhou',
  }[status] || status;
}

function formatBytes(bytes) {
  if (!bytes) return '0 B';
  const units = ['B', 'KB', 'MB', 'GB'];
  const index = Math.min(units.length - 1, Math.floor(Math.log(bytes) / Math.log(1024)));
  return `${(bytes / 1024 ** index).toFixed(index ? 1 : 0)} ${units[index]}`;
}

function renderCollections() {
  const list = byId('collection-list');
  list.replaceChildren();
  const jobs = state.bootstrap.collectionJobs || [];
  if (!jobs.length) {
    const empty = document.createElement('div');
    empty.className = 'collection-empty';
    empty.textContent = 'Nenhuma coleta por link iniciada nesta sessão.';
    list.append(empty);
    return;
  }
  jobs.slice(0, 8).forEach((job) => {
    const collectionFinished = ['completed', 'needs_attention'].includes(job.status);
    const row = document.createElement('article');
    row.className = 'collection-row';
    const signal = document.createElement('span');
    signal.className = `collection-signal ${job.status}`;
    const copy = document.createElement('div');
    const title = document.createElement('strong');
    title.textContent = job.sourceName;
    const url = document.createElement('small');
    url.textContent = job.url;
    const stats = document.createElement('div');
    stats.className = 'collection-engine-stats';
    const telemetry = job.telemetry || {};
    [
      `${job.capacityProfile || 'balanced'}`,
      `${telemetry.requests || 0} req`,
      `${formatBytes(telemetry.bytes || 0)}`,
      `${telemetry.cacheHits || 0} cache`,
      `${telemetry.retries || 0} retry`,
    ].forEach((label) => {
      const item = document.createElement('span');
      item.textContent = label;
      stats.append(item);
    });
    copy.append(title, url, stats);
    const result = document.createElement('div');
    result.className = 'collection-result';
    const status = document.createElement('strong');
    status.textContent = collectionStatusLabel(job.status);
    const summary = document.createElement('small');
    const flowSummary = [
      `${job.discoveredDocuments || 0} descobertos`,
      `${job.downloadedDocuments || job.documents || 0} baixados`,
      `${job.processedDocuments || 0} processados`,
      `${job.skippedDocuments || 0} já processados`,
      `${job.questions || 0} questões`,
      `${(job.failures || 0) + (job.failedDocuments || 0)} falhas`,
    ].join(' · ');
    if (job.status === 'failed') summary.textContent = job.error || 'Falha não detalhada.';
    else if (collectionFinished) {
      summary.textContent = flowSummary;
      if (!job.documents) status.textContent = 'Concluída sem PDFs';
      else if (job.skippedDocuments === job.documents) status.textContent = 'Já processado — ignorado';
    } else if (job.status === 'processing') {
      summary.textContent = flowSummary;
    } else summary.textContent = 'A página e os PDFs estão sendo verificados.';
    result.append(status, summary);
    const actions = document.createElement('div');
    actions.className = 'collection-actions';
    if (['queued', 'running'].includes(job.status)) {
      actions.append(
        collectionButton('Pausar', () => collectionAction(job.id, 'pause')),
        collectionButton('Cancelar', () => collectionAction(job.id, 'cancel')),
      );
    } else if (job.status === 'paused') {
      actions.append(collectionButton('Continuar', () => collectionAction(job.id, 'resume')));
    }
    result.append(actions);
    row.append(signal, copy, result);
    if (collectionFinished) {
      const details = document.createElement('details');
      details.className = 'collection-details';
      const detailsSummary = document.createElement('summary');
      detailsSummary.textContent = job.documents
        ? `Ver ${job.documents} arquivo(s) e pasta de destino`
        : 'Ver motivo e pasta de destino';
      details.append(detailsSummary);

      if (job.outputDirectory) {
        const directory = document.createElement('code');
        directory.className = 'collection-directory';
        directory.textContent = job.outputDirectory;
        details.append(directory);
      }

      if ((job.files || []).length) {
        const files = document.createElement('ul');
        files.className = 'collection-files';
        job.files.forEach((file) => {
          const item = document.createElement('li');
          const name = document.createElement('strong');
          name.textContent = file.title;
          const path = document.createElement('small');
          path.textContent = file.localPath;
          item.append(name, path);
          files.append(item);
        });
        details.append(files);
      }

      const notices = [
        ...(job.warnings || []),
        ...(job.failureDetails || []).map((failure) => failure.message),
      ];
      if (notices.length) {
        const warnings = document.createElement('ul');
        warnings.className = 'collection-warnings';
        [...new Set(notices)].forEach((message) => {
          const item = document.createElement('li');
          item.textContent = message;
          warnings.append(item);
        });
        details.append(warnings);
      }
      row.append(details);
    }
    list.append(row);
  });
}

function collectionButton(label, action) {
  const button = document.createElement('button');
  button.type = 'button';
  button.textContent = label;
  button.addEventListener('click', action);
  return button;
}

async function collectionAction(collectionId, action) {
  try {
    await request(`/api/collections/${collectionId}/${action}`, {method: 'POST', body: '{}'});
    toast(action === 'resume' ? 'Coleta retomada do checkpoint.' : 'Comando enviado ao motor.');
    await loadBootstrap({preserveQuery: true});
  } catch (error) { toast(error.message, 'error'); }
}

function renderMetrics() {
  const summary = state.bootstrap.summary || {};
  byId('metric-total').textContent = summary.total || 0;
  byId('metric-answer-summary').textContent =
    `${summary.answer_official || 0} vinculadas ao gabarito · ${summary.answer_missing || 0} sem resposta oficial`;
  byId('metric-pending').textContent = summary.pending || 0;
  byId('metric-exceptions').textContent = summary.exception || 0;
  byId('metric-missing-answers').textContent = `${summary.answer_missing || 0} sem resposta oficial`;
  byId('metric-exportable').textContent = summary.exportable || 0;
  const activeStatus = state.filters.statuses.length === 1 ? state.filters.statuses[0] : null;
  byId('metric-card-pending').classList.toggle('active', activeStatus === 'pending');
  byId('metric-card-exceptions').classList.toggle('active', activeStatus === 'exception');
  byId('metric-card-exportable').classList.toggle('active', activeStatus === 'exportable');
}

function renderJobs() {
  const strip = byId('job-strip');
  strip.replaceChildren();
  const activeOrRecent = (state.bootstrap.jobs || []).filter((job, index) =>
    ['queued', 'running', 'cancelling', 'paused', 'failed'].includes(job.status) || index < 2
  );
  activeOrRecent.forEach((job) => {
    const progress = job.total_pages ? Math.min(100, Math.round(job.processed_pages / job.total_pages * 100)) : 0;
    const card = document.createElement('article');
    card.className = 'job-card';

    const rail = document.createElement('div');
    rail.className = 'job-rail';
    const railFill = document.createElement('span');
    railFill.style.height = `${Math.max(5, progress)}%`;
    rail.append(railFill);

    const title = document.createElement('div');
    title.className = 'job-title';
    const strong = document.createElement('strong');
    strong.textContent = job.current_file || `Lote ${job.id.slice(0, 8)}`;
    const detail = document.createElement('span');
    detail.textContent = job.error || job.message || statusLabel(job.status);
    title.append(strong, detail);

    const progressBlock = document.createElement('div');
    const track = document.createElement('div');
    track.className = 'progress-track';
    const fill = document.createElement('span');
    fill.style.width = `${progress}%`;
    track.append(fill);
    const copy = document.createElement('span');
    copy.className = 'job-progress-copy';
    const eta = job.eta_seconds ? ` · cerca de ${formatDuration(job.eta_seconds)}` : '';
    copy.textContent = `${job.processed_pages}/${job.total_pages || '—'} páginas · ${progress}%${eta}`;
    progressBlock.append(track, copy);

    const actions = document.createElement('div');
    actions.className = 'job-actions';
    if (['running', 'queued'].includes(job.status)) {
      actions.append(jobButton('Pausar', () => jobAction(job.id, 'cancel')));
    } else if (['paused', 'failed'].includes(job.status)) {
      actions.append(jobButton('Retomar', () => jobAction(job.id, 'resume')));
    } else {
      const pill = document.createElement('span');
      pill.className = `status-pill ${job.status}`;
      pill.textContent = job.status === 'completed' ? 'Concluído' : statusLabel(job.status);
      actions.append(pill);
    }
    card.append(rail, title, progressBlock, actions);
    strip.append(card);
  });
}

function jobButton(label, onClick) {
  const button = document.createElement('button');
  button.type = 'button';
  button.className = 'job-action';
  button.textContent = label;
  button.addEventListener('click', onClick);
  return button;
}

function formatDuration(seconds) {
  if (seconds < 60) return `${seconds}s`;
  const minutes = Math.ceil(seconds / 60);
  return `${minutes} min`;
}

async function jobAction(jobId, action) {
  try {
    await request(`/api/jobs/${jobId}/${action}`, {method: 'POST', body: '{}'});
    toast(action === 'cancel' ? 'Pausa solicitada. O checkpoint atual será preservado.' : 'Processamento retomado.');
    await loadBootstrap({preserveQuery: true});
  } catch (error) { toast(error.message, 'error'); }
}

function schedulePoll() {
  clearTimeout(state.polling);
  const activeProcessing = (state.bootstrap.jobs || []).some((job) =>
    ['queued', 'running', 'cancelling'].includes(job.status));
  const activeCollection = (state.bootstrap.collectionJobs || []).some((job) =>
    ['queued', 'running', 'pausing', 'cancelling', 'processing'].includes(job.status));
  const active = activeProcessing || activeCollection;
  if (active) {
    state.polling = setTimeout(() => loadBootstrap({preserveQuery: true}).catch(() => {}), 1400);
  }
}

async function submitSourceCollection(event) {
  event.preventDefault();
  const button = byId('source-submit');
  button.disabled = true;
  try {
    const result = await request('/api/collections', {
      method: 'POST',
      body: JSON.stringify({
        sourceId: byId('source-select').value,
        url: byId('source-url').value.trim(),
        classifierProvider: byId('source-classifier').value,
        capacityProfile: byId('source-capacity-profile').value,
        browserEnabled: byId('source-browser-enabled').checked,
        maxConcurrency: Number(byId('source-max-concurrency').value),
        requestIntervalSeconds: Number(byId('source-request-interval').value),
        robotsPolicy: byId('source-robots-policy').value,
        crawlDelayPolicy: byId('source-crawl-delay-policy').value,
      }),
    });
    toast(`Coleta ${result.collectionId.slice(0, 8)} iniciada. A janela pode continuar sendo usada.`);
    await loadBootstrap({preserveQuery: true});
  } catch (error) {
    toast(error.message, 'error');
  } finally {
    const source = sourceById(byId('source-select').value);
    button.disabled = !source?.collectable;
  }
}

function applyCapacityProfile() {
  const profile = byId('source-capacity-profile').value;
  const concurrency = byId('source-max-concurrency');
  const interval = byId('source-request-interval');
  const presets = {
    conservative: [2, 3], balanced: [4, 1], high_performance: [8, 0],
  };
  if (presets[profile]) {
    [concurrency.value, interval.value] = presets[profile];
  }
  const editable = profile === 'custom';
  concurrency.readOnly = !editable;
  interval.readOnly = !editable;
}

function renderQuery() {
  if (!state.query) return;
  const visiblePending = new Set(
    (state.query.questions || []).filter((view) => view.status === 'pending').map((view) => view.id)
  );
  state.selectedQuestionIds = new Set(
    [...state.selectedQuestionIds].filter((questionId) => visiblePending.has(questionId))
  );
  byId('result-count').textContent = state.query.total || 0;
  const activeStatus = state.filters.statuses.length === 1 ? state.filters.statuses[0] : null;
  byId('records-kicker').textContent = activeStatus === 'pending' ? 'FILA DE REVISÃO'
    : activeStatus === 'exception' ? 'QUESTÕES EM EXCEÇÃO'
      : activeStatus === 'exportable' ? 'QUESTÕES APROVADAS' : 'QUESTÕES ENCONTRADAS';
  renderFacets();
  renderActiveFilters();
  renderQuestions();
  renderBatchToolbar();
  renderMetrics();
}

function renderFacets() {
  const root = byId('facet-groups');
  root.replaceChildren();
  facetDefinitions.forEach(([sectionName, facets], sectionIndex) => {
    const sectionLabel = document.createElement('p');
    sectionLabel.className = 'eyebrow facet-section-label';
    sectionLabel.textContent = sectionName;
    sectionLabel.style.marginTop = sectionIndex ? '12px' : '2px';
    root.append(sectionLabel);
    facets.forEach(([key, label], index) => {
      const options = state.query.facets[key] || [];
      const details = document.createElement('details');
      details.className = 'facet-group';
      details.open = index === 0 || state.filters[key].length > 0;
      const summary = document.createElement('summary');
      const name = document.createElement('span');
      name.textContent = label;
      summary.append(name);
      const list = document.createElement('div');
      list.className = 'facet-options';
      if (!options.length) {
        const empty = document.createElement('span');
        empty.className = 'facet-option';
        empty.textContent = 'Sem opções neste recorte';
        list.append(empty);
      }
      options.slice(0, 40).forEach((option) => {
        const optionLabel = document.createElement('label');
        optionLabel.className = 'facet-option';
        optionLabel.dataset.search = String(option.value).toLocaleLowerCase('pt-BR');
        const checkbox = document.createElement('input');
        checkbox.type = 'checkbox';
        checkbox.checked = Boolean(option.selected);
        checkbox.addEventListener('change', () => toggleFilter(key, option.value));
        const copy = document.createElement('strong');
        copy.textContent = key === 'quality_flags' ? flagLabel(option.value)
          : key === 'statuses' ? statusLabel(option.value) : String(option.value);
        const count = document.createElement('span');
        count.textContent = option.count;
        optionLabel.append(checkbox, copy, count);
        list.append(optionLabel);
      });
      details.append(summary, list);
      root.append(details);
    });
  });
  filterFacetOptions();
}

async function toggleFilter(key, value) {
  const values = state.filters[key];
  const existing = values.findIndex((item) => String(item) === String(value));
  if (existing >= 0) values.splice(existing, 1);
  else values.push(key === 'years' ? Number(value) : value);
  await runQuery();
}

function renderActiveFilters() {
  const root = byId('active-filters');
  root.replaceChildren();
  Object.entries(state.filters).forEach(([key, value]) => {
    if (Array.isArray(value)) {
      value.forEach((item) => root.append(filterChip(key, item)));
    } else if (key === 'search' && value) {
      root.append(filterChip(key, value));
    } else if (key === 'min_confidence' && value !== null) {
      root.append(filterChip(key, `${Math.round(value * 100)}%`));
    }
  });
  if (!root.children.length) {
    const copy = document.createElement('span');
    copy.className = 'muted-filter-copy';
    copy.textContent = 'Nenhum filtro ativo';
    copy.style.cssText = 'color: var(--subtle); font-size: 9px;';
    root.append(copy);
  }
}

function filterChip(key, value) {
  const button = document.createElement('button');
  button.type = 'button';
  button.className = 'filter-chip';
  button.textContent = `${key === 'quality_flags' ? flagLabel(value) : key === 'statuses' ? statusLabel(value) : value} ×`;
  button.addEventListener('click', async () => {
    if (Array.isArray(state.filters[key])) {
      state.filters[key] = state.filters[key].filter((item) => String(item) !== String(value));
    } else if (key === 'min_confidence') state.filters[key] = null;
    else state.filters[key] = '';
    if (key === 'search') byId('question-search').value = '';
    await runQuery();
  });
  return button;
}

function renderQuestions() {
  const root = byId('question-list');
  root.replaceChildren();
  const questions = state.query.questions || [];
  if (!questions.length) {
    const empty = document.createElement('div');
    empty.className = 'empty-state';
    const mark = document.createElement('div');
    mark.className = 'empty-mark';
    mark.textContent = 'K';
    const strong = document.createElement('strong');
    strong.textContent = state.bootstrap.summary.total ? 'Nenhuma questão neste recorte' : 'Seu primeiro lote começa aqui';
    const copy = document.createElement('span');
    copy.textContent = state.bootstrap.summary.total ? 'Remova filtros ou ajuste a busca.' : 'Adicione um PDF textual para iniciar a extração.';
    empty.append(mark, strong, copy);
    root.append(empty);
    return;
  }
  questions.forEach((view) => {
    const question = view.question;
    const row = document.createElement('article');
    row.className = 'question-row';
    row.classList.toggle('selected', state.selectedQuestionIds.has(view.id));

    if (view.status === 'pending') {
      const selector = document.createElement('label');
      selector.className = 'question-selector';
      selector.setAttribute('aria-label', `Selecionar questão ${question.number}`);
      const checkbox = document.createElement('input');
      checkbox.type = 'checkbox';
      checkbox.checked = state.selectedQuestionIds.has(view.id);
      checkbox.addEventListener('change', () => toggleQuestionSelection(view.id));
      selector.append(checkbox);
      row.append(selector);
    } else {
      const placeholder = document.createElement('span');
      placeholder.className = 'question-selector-placeholder';
      row.append(placeholder);
    }

    const open = document.createElement('button');
    open.type = 'button';
    open.className = 'question-open';
    open.addEventListener('click', () => openQuestion(view.id));

    const main = document.createElement('span');
    main.className = 'question-copy';
    const number = document.createElement('span');
    number.className = 'question-number';
    number.textContent = String(question.number).padStart(2, '0');
    const text = document.createElement('span');
    const title = document.createElement('strong');
    title.textContent = question.statement;
    const source = document.createElement('small');
    source.textContent = view.status === 'exception' && view.review_notes
      ? `Motivo: ${view.review_notes}`
      : `${view.filename} · pág. ${(question.source_pages || []).join(', ') || '—'}`;
    text.append(title, source);
    main.append(number, text);

    const classification = document.createElement('span');
    classification.className = 'classification-copy';
    const discipline = document.createElement('strong');
    discipline.textContent = question.discipline || 'Sem disciplina';
    const topic = document.createElement('small');
    topic.textContent = [question.matter, question.subject].filter(Boolean).join(' · ') || 'Classificação pendente';
    classification.append(discipline, topic);

    const confidence = document.createElement('span');
    confidence.className = 'confidence';
    const percentage = Math.round((view.confidence || 0) * 100);
    confidence.append(document.createTextNode(`${percentage}%`));
    const bar = document.createElement('span');
    bar.className = 'confidence-bar';
    const fill = document.createElement('span');
    fill.style.width = `${percentage}%`;
    bar.append(fill);
    confidence.append(bar);

    const status = document.createElement('span');
    status.className = `status-pill ${view.status}`;
    status.textContent = view.exportable ? 'Exportável' : statusLabel(view.status);
    open.append(main, classification, confidence, status);
    row.append(open);
    root.append(row);
  });
}

function pendingQuestionsInView() {
  return (state.query?.questions || []).filter((view) => view.status === 'pending');
}

function toggleQuestionSelection(questionId) {
  if (state.selectedQuestionIds.has(questionId)) state.selectedQuestionIds.delete(questionId);
  else state.selectedQuestionIds.add(questionId);
  renderQuestions();
  renderBatchToolbar();
}

function renderBatchToolbar() {
  const pending = pendingQuestionsInView();
  const toolbar = byId('batch-toolbar');
  toolbar.hidden = pending.length === 0;
  const selected = state.selectedQuestionIds.size;
  byId('selection-summary').textContent = `${selected} selecionada${selected === 1 ? '' : 's'}`;
  byId('batch-approve-open').disabled = selected === 0;
  const selectAll = byId('select-all-pending');
  selectAll.checked = pending.length > 0 && selected === pending.length;
  selectAll.indeterminate = selected > 0 && selected < pending.length;
}

function activateEditorialQueue(status) {
  state.activeSection = 'workspace';
  state.filters = emptyFilters();
  state.filters.statuses = [status];
  state.selectedQuestionIds.clear();
  byId('question-search').value = '';
  document.querySelectorAll('.rail-link').forEach((item) => item.classList.remove('active'));
  document.querySelector('.rail-link[data-section="workspace"]')?.classList.add('active');
  renderSection();
  runQuery().then(() => {
    document.querySelector('.workbench')?.scrollIntoView({behavior: 'smooth', block: 'start'});
  }).catch((error) => toast(error.message, 'error'));
}

function filterFacetOptions() {
  const query = byId('facet-search').value.trim().toLocaleLowerCase('pt-BR');
  document.querySelectorAll('.facet-option[data-search]').forEach((option) => {
    option.hidden = Boolean(query) && !option.dataset.search.includes(query);
  });
}

function renderSavedFilters() {
  const select = byId('saved-filter-select');
  const selected = select.value;
  select.replaceChildren(new Option('Filtros salvos', ''));
  (state.bootstrap.savedFilters || []).forEach((item) => {
    select.append(new Option(item.name, item.id));
  });
  select.value = selected;
}

async function saveCurrentFilter() {
  const name = window.prompt('Nome do filtro profissional:');
  if (!name) return;
  try {
    await request('/api/filters', {
      method: 'POST', body: JSON.stringify({name, filters: state.filters}),
    });
    toast('Filtro salvo para reutilização.');
    await loadBootstrap({preserveQuery: true});
  } catch (error) { toast(error.message, 'error'); }
}

async function applySavedFilter(id) {
  const item = (state.bootstrap.savedFilters || []).find((candidate) => candidate.id === id);
  if (!item) return;
  state.filters = {...emptyFilters(), ...structuredClone(item.filters)};
  byId('question-search').value = state.filters.search || '';
  await runQuery();
}

async function choosePaths(kind) {
  let paths = [];
  try {
    if (window.pywebview?.api) {
      paths = kind === 'files'
        ? await window.pywebview.api.choose_pdfs()
        : await window.pywebview.api.choose_folder();
    } else {
      const raw = window.prompt(kind === 'files' ? 'Cole o caminho completo do PDF:' : 'Cole o caminho completo da pasta:');
      if (raw) paths = [raw];
    }
  } catch (error) {
    toast(`Não foi possível abrir o seletor: ${error}`, 'error');
  }
  if (paths?.length) {
    state.selectedPaths = [...new Set([...state.selectedPaths, ...paths])];
    renderSelectedPaths();
  }
}

function renderSelectedPaths() {
  const root = byId('selected-paths');
  root.replaceChildren();
  state.selectedPaths.forEach((path) => {
    const chip = document.createElement('span');
    chip.className = 'path-chip';
    chip.title = path;
    chip.textContent = path;
    root.append(chip);
  });
  byId('import-hint').textContent = state.selectedPaths.length
    ? `${state.selectedPaths.length} caminho(s) selecionado(s).`
    : 'Nenhum arquivo selecionado.';
}

function importMetadata() {
  return {
    provider: optional('import-provider'), source_url: optional('import-url'),
    concurso: optional('import-concurso'), board: optional('import-board'),
    year: numberOrNull('import-year'), role: optional('import-role'),
    organization: optional('import-organization'), level: optional('import-level'),
    discipline: optional('import-discipline'), subject: optional('import-subject'),
    topic: optional('import-topic'), difficulty: optional('import-difficulty'),
    document_type: byId('import-document-type').value,
  };
}

async function submitImport(event) {
  event.preventDefault();
  if (!state.selectedPaths.length) {
    toast('Selecione ao menos um PDF ou uma pasta.', 'error');
    return;
  }
  byId('import-submit').disabled = true;
  try {
    const result = await request('/api/import', {
      method: 'POST',
      body: JSON.stringify({
        paths: state.selectedPaths,
        metadata: importMetadata(),
        classifierProvider: byId('import-classifier').value,
      }),
    });
    byId('import-dialog').close();
    state.selectedPaths = [];
    renderSelectedPaths();
    toast(result.jobIds.length
      ? 'Lote iniciado. Você pode pausar e retomar sem perder páginas processadas.'
      : 'Arquivo já conhecido; nenhuma nova tarefa foi criada.');
    await loadBootstrap({preserveQuery: true});
  } catch (error) { toast(error.message, 'error'); }
  finally { byId('import-submit').disabled = false; }
}

async function openQuestion(questionId) {
  try {
    const payload = await request(`/api/questions/${questionId}`);
    state.currentQuestion = payload.question;
    state.currentAudit = payload.audit;
    fillReviewForm();
    byId('review-dialog').showModal();
  } catch (error) { toast(error.message, 'error'); }
}

function fillReviewForm() {
  const view = state.currentQuestion;
  const question = view.question;
  byId('review-title').textContent = `Questão ${question.number} · ${view.filename}`;
  byId('review-pdf').href = `/api/documents/${view.document_id}/pdf`;
  byId('review-pdf').dataset.page = question.source_pages?.[0] || '';
  byId('review-pdf').textContent = question.source_pages?.[0]
    ? `Abrir PDF · pág. ${question.source_pages[0]}` : 'Abrir PDF';
  byId('edit-statement').value = question.statement;
  byId('edit-answer-status').value = question.answer_status;
  byId('edit-source-pages').value = (question.source_pages || []).join(', ');
  byId('edit-explanation').value = question.explanation || '';
  byId('edit-discipline').value = question.discipline || '';
  byId('edit-matter').value = question.matter || '';
  byId('edit-subject').value = question.subject || '';
  byId('edit-board').value = question.board || '';
  byId('edit-concurso').value = question.concurso || '';
  byId('edit-year').value = question.year || '';
  byId('edit-role').value = question.role || '';
  byId('edit-variant').value = view.metadata.variant || '';
  byId('edit-organization').value = question.organization || '';
  byId('edit-level').value = question.level || '';
  byId('edit-difficulty').value = question.difficulty || '';
  byId('edit-provider').value = view.metadata.provider || '';
  byId('edit-source-url').value = view.metadata.source_url || '';
  byId('edit-review-notes').value = (question.review_notes || []).join('\n');
  byId('edit-actor').value = view.reviewer || '';
  byId('edit-decision-notes').value = view.review_notes || '';
  renderAlternativeEditor(question.alternatives);
  renderCorrectAnswers(question.correct_answer);
  byId('edit-correct-answer').disabled = question.answer_status !== 'matched';
  renderReviewFlags();
  renderReviewContext();
  byId('review-status-copy').textContent = `Situação atual: ${statusLabel(view.status)} · ${state.currentAudit.length} evento(s) de auditoria`;
}

function renderReviewContext() {
  const root = byId('review-context');
  root.replaceChildren();
  const view = state.currentQuestion;
  const question = view.question;
  const items = [
    ['Banca', question.board || view.metadata.board],
    ['Concurso', question.concurso || view.metadata.concurso],
    ['Ano', question.year || view.metadata.year],
    ['Cargo', question.role || view.metadata.role],
    ['Variante', view.metadata.variant],
    ['Documento', view.filename],
  ];
  items.forEach(([label, value]) => {
    const item = document.createElement('div');
    item.className = 'review-context-item';
    const caption = document.createElement('span');
    caption.textContent = label;
    const content = document.createElement('strong');
    content.textContent = value || 'Não informado';
    content.title = String(value || 'Não informado');
    item.append(caption, content);
    root.append(item);
  });
}

function renderReviewFlags() {
  const root = byId('review-flags');
  root.replaceChildren();
  const flags = state.currentQuestion.flags || [];
  if (!flags.length) {
    const pill = document.createElement('span');
    pill.className = 'status-pill approved';
    pill.textContent = 'Sem bloqueios automáticos';
    root.append(pill);
  }
  flags.forEach((flag) => {
    const pill = document.createElement('span');
    pill.className = 'flag-pill';
    pill.textContent = flagLabel(flag);
    root.append(pill);
  });
}

function renderAlternativeEditor(alternatives) {
  const root = byId('edit-alternatives');
  root.replaceChildren();
  alternatives.forEach((alternative, index) => {
    const row = document.createElement('div');
    row.className = 'alternative-row';
    const letter = document.createElement('input');
    letter.className = 'alternative-letter';
    letter.value = String.fromCharCode(65 + index);
    letter.readOnly = true;
    const text = document.createElement('input');
    text.className = 'alternative-text';
    text.value = alternative.text;
    text.setAttribute('aria-label', `Alternativa ${letter.value}`);
    const remove = document.createElement('button');
    remove.type = 'button';
    remove.className = 'remove-alternative';
    remove.textContent = '×';
    remove.setAttribute('aria-label', `Remover alternativa ${letter.value}`);
    remove.addEventListener('click', () => {
      const current = collectAlternatives();
      current.splice(index, 1);
      renderAlternativeEditor(current);
      renderCorrectAnswers();
    });
    row.append(letter, text, remove);
    root.append(row);
  });
}

function collectAlternatives() {
  return [...document.querySelectorAll('#edit-alternatives .alternative-row')].map((row, index) => ({
    letter: String.fromCharCode(65 + index),
    text: row.querySelector('.alternative-text').value.trim(),
  }));
}

function renderCorrectAnswers(selected) {
  const select = byId('edit-correct-answer');
  const previous = selected ?? select.value;
  select.replaceChildren(new Option('—', ''));
  collectAlternatives().forEach((item) => select.append(new Option(item.letter, item.letter)));
  select.value = previous || '';
}

function collectQuestion() {
  const original = state.currentQuestion.question;
  const answerStatus = byId('edit-answer-status').value;
  const correct = optional('edit-correct-answer');
  return {
    ...original,
    statement: byId('edit-statement').value.trim(),
    alternatives: collectAlternatives(),
    answer_status: answerStatus,
    correct_answer: answerStatus === 'matched' ? correct : null,
    source_pages: byId('edit-source-pages').value.split(',').map((item) => Number(item.trim())).filter((item) => Number.isInteger(item) && item > 0),
    explanation: optional('edit-explanation'), discipline: optional('edit-discipline'),
    matter: optional('edit-matter'), subject: optional('edit-subject'),
    board: optional('edit-board'), concurso: optional('edit-concurso'),
    year: numberOrNull('edit-year'), role: optional('edit-role'),
    organization: optional('edit-organization'), level: optional('edit-level'),
    difficulty: optional('edit-difficulty'),
    review_notes: byId('edit-review-notes').value.split('\n').map((item) => item.trim()).filter(Boolean),
  };
}

function humanClassification(question) {
  const classification = structuredClone(state.currentQuestion.classification);
  const mapping = {
    discipline: question.discipline, subject: question.matter, topic: question.subject,
    board: question.board, concurso: question.concurso, year: question.year,
    role: question.role, organization: question.organization, level: question.level,
    difficulty: question.difficulty,
  };
  Object.entries(mapping).forEach(([key, value]) => {
    classification[key] = {
      value, confidence: value === null ? 0 : 1,
      evidence: value === null ? null : 'Confirmado na revisão humana',
    };
  });
  return classification;
}

function documentMetadata(question) {
  return {
    ...state.currentQuestion.metadata,
    provider: optional('edit-provider'), source_url: optional('edit-source-url'),
    variant: optional('edit-variant'),
    concurso: question.concurso, board: question.board, year: question.year,
    role: question.role, organization: question.organization, level: question.level,
    discipline: question.discipline, subject: question.matter, topic: question.subject,
    difficulty: question.difficulty,
  };
}

async function saveCurrentQuestion({silent = false} = {}) {
  const actor = optional('edit-actor');
  if (!actor) throw new Error('Informe o revisor responsável.');
  const question = collectQuestion();
  await request(`/api/questions/${state.currentQuestion.id}`, {
    method: 'PUT',
    body: JSON.stringify({
      question, classification: humanClassification(question), actor,
      notes: optional('edit-decision-notes'),
    }),
  });
  await request(`/api/documents/${state.currentQuestion.document_id}`, {
    method: 'PUT', body: JSON.stringify({metadata: documentMetadata(question), actor}),
  });
  if (!silent) toast('Alterações salvas e registradas na auditoria.');
  const refreshed = await request(`/api/questions/${state.currentQuestion.id}`);
  state.currentQuestion = refreshed.question;
  state.currentAudit = refreshed.audit;
  fillReviewForm();
}

async function decideCurrent(status) {
  try {
    const actor = optional('edit-actor');
    const notes = optional('edit-decision-notes');
    if (!actor) throw new Error('Informe o revisor responsável.');
    if (['exception', 'rejected'].includes(status) && !notes) {
      throw new Error('Informe a justificativa para esta decisão.');
    }
    if (status !== 'pending') await saveCurrentQuestion({silent: true});
    await request(`/api/questions/${state.currentQuestion.id}/decision`, {
      method: 'POST',
      body: JSON.stringify({status, actor, notes}),
    });
    const messages = {
      approved: 'Questão aprovada e movida para Exportáveis.',
      exception: 'Questão enviada para Exceções com a justificativa informada.',
      rejected: 'Questão rejeitada e decisão registrada.',
      pending: 'Questão mantida em Pendentes para revisão posterior.',
    };
    toast(messages[status] || 'Decisão registrada.');
    state.selectedQuestionIds.delete(state.currentQuestion.id);
    byId('review-dialog').close();
    await loadBootstrap({preserveQuery: true});
  } catch (error) { toast(error.message, 'error'); }
}

function openBatchApproval() {
  const count = state.selectedQuestionIds.size;
  if (!count) return;
  const noun = count === 1 ? 'questão' : 'questões';
  byId('batch-confirm-copy').textContent = `Você está prestes a aprovar ${count} ${noun} e movê-la${count === 1 ? '' : 's'} de Pendentes para Exportáveis.`;
  byId('batch-actor').value = byId('edit-actor').value || '';
  byId('batch-notes').value = '';
  byId('batch-approve-dialog').showModal();
}

async function submitBatchApproval(event) {
  event.preventDefault();
  const button = byId('batch-approve-submit');
  const questionIds = [...state.selectedQuestionIds];
  button.disabled = true;
  try {
    const result = await request('/api/questions/batch-approve', {
      method: 'POST',
      body: JSON.stringify({
        questionIds,
        actor: byId('batch-actor').value.trim(),
        notes: byId('batch-notes').value.trim() || null,
      }),
    });
    byId('batch-approve-dialog').close();
    state.selectedQuestionIds.clear();
    const noun = result.approved === 1 ? 'questão' : 'questões';
    toast(`${result.approved} ${noun} aprovada${result.approved === 1 ? '' : 's'} e movida${result.approved === 1 ? '' : 's'} para Exportáveis.`);
    await loadBootstrap({preserveQuery: true});
  } catch (error) {
    toast(error.message, 'error');
  } finally {
    button.disabled = false;
  }
}

async function exportCurrentFilter() {
  let outputPath = null;
  try {
    if (window.pywebview?.api) outputPath = await window.pywebview.api.choose_export_folder();
    else outputPath = window.prompt('Pasta de destino (deixe vazio para usar a pasta padrão):') || null;
    if (window.pywebview?.api && !outputPath) return;
    const result = await request('/api/export', {
      method: 'POST', body: JSON.stringify({filters: state.filters, outputPath}),
    });
    toast(`Exportação concluída: ${result.exported} válida(s), ${result.exceptions} exceção(ões). Pasta: ${result.directory}`);
    await loadBootstrap({preserveQuery: false});
  } catch (error) { toast(error.message, 'error'); }
}

function debounce(callback, delay = 260) {
  let timer;
  return (...args) => {
    clearTimeout(timer);
    timer = setTimeout(() => callback(...args), delay);
  };
}

document.querySelectorAll('.modal-close').forEach((button) => {
  button.addEventListener('click', () => button.closest('dialog').close());
});
byId('import-open').addEventListener('click', () => byId('import-dialog').showModal());
byId('export-open').addEventListener('click', exportCurrentFilter);
byId('metric-card-pending').addEventListener('click', () => activateEditorialQueue('pending'));
byId('metric-card-exceptions').addEventListener('click', () => activateEditorialQueue('exception'));
byId('metric-card-exportable').addEventListener('click', () => activateEditorialQueue('exportable'));
byId('review-pdf').addEventListener('click', openAuthenticatedPdf);
byId('choose-files').addEventListener('click', () => choosePaths('files'));
byId('choose-folder').addEventListener('click', () => choosePaths('folder'));
byId('import-form').addEventListener('submit', submitImport);
byId('source-form').addEventListener('submit', submitSourceCollection);
byId('source-select').addEventListener('change', (event) => selectSource(event.target.value));
byId('source-capacity-profile').addEventListener('change', applyCapacityProfile);
byId('facet-search').addEventListener('input', filterFacetOptions);
byId('clear-filters').addEventListener('click', async () => {
  state.filters = emptyFilters();
  state.selectedQuestionIds.clear();
  byId('question-search').value = '';
  await runQuery();
});
byId('question-search').addEventListener('input', debounce(async (event) => {
  state.filters.search = event.target.value.trim();
  await runQuery();
}));
byId('save-filter').addEventListener('click', saveCurrentFilter);
byId('saved-filter-select').addEventListener('change', (event) => applySavedFilter(event.target.value));
byId('add-alternative').addEventListener('click', () => {
  const alternatives = collectAlternatives();
  if (alternatives.length >= 5) return toast('O contrato aceita no máximo cinco alternativas.', 'error');
  alternatives.push({letter: String.fromCharCode(65 + alternatives.length), text: ''});
  renderAlternativeEditor(alternatives);
  renderCorrectAnswers();
});
byId('edit-answer-status').addEventListener('change', (event) => {
  byId('edit-correct-answer').disabled = event.target.value !== 'matched';
  if (event.target.value !== 'matched') byId('edit-correct-answer').value = '';
});
byId('save-question').addEventListener('click', () => saveCurrentQuestion().catch((error) => toast(error.message, 'error')));
byId('approve-question').addEventListener('click', () => decideCurrent('approved'));
byId('reject-question').addEventListener('click', () => decideCurrent('rejected'));
byId('mark-exception').addEventListener('click', () => decideCurrent('exception'));
byId('defer-question').addEventListener('click', () => decideCurrent('pending'));
byId('select-all-pending').addEventListener('change', (event) => {
  state.selectedQuestionIds = event.target.checked
    ? new Set(pendingQuestionsInView().map((view) => view.id)) : new Set();
  renderQuestions();
  renderBatchToolbar();
});
byId('clear-selection').addEventListener('click', () => {
  state.selectedQuestionIds.clear();
  renderQuestions();
  renderBatchToolbar();
});
byId('batch-approve-open').addEventListener('click', openBatchApproval);
byId('batch-approve-form').addEventListener('submit', submitBatchApproval);
document.querySelectorAll('.rail-link').forEach((button) => {
  button.addEventListener('click', async () => {
    document.querySelectorAll('.rail-link').forEach((item) => item.classList.remove('active'));
    button.classList.add('active');
    const section = button.dataset.section;
    state.activeSection = section;
    state.selectedQuestionIds.clear();
    renderSection();
    if (section === 'sources') return;
    state.filters.statuses = section === 'reviews'
      ? ['pending', 'exception']
      : section === 'exports' ? ['exportable', 'exported'] : [];
    await runQuery();
  });
});

applyCapacityProfile();
loadBootstrap().catch((error) => toast(error.message, 'error'));
