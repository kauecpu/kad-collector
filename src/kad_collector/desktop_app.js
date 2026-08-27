function semanticIdentityBadge(view) {
  return {
    exact_duplicate: 'Duplicata exata',
    republication: 'Republicação',
    new_identity: 'Nova versão',
    new_version: 'Nova versão',
    uncertain: 'Exceção',
  }[view?.resolution] || null;
}

function semanticIdentityPresentation(view) {
  const identity = view?.identity || {};
  const fieldValue = (name) => {
    const field = identity[name] || {};
    const values = field.normalized_values || [];
    return values.join(', ');
  };
  const fields = [
    ['Banca', fieldValue('board')],
    ['Concurso', fieldValue('concurso')],
    ['Ano', fieldValue('year')],
    ['Cargo', fieldValue('roles')],
    ['Turno', fieldValue('turns')],
    ['Tipo', fieldValue('variants')],
  ].filter(([, value]) => value);
  return {
    identityLabel: view?.identityStatus === 'known' ? 'Identidade reconhecida' : 'Identidade desconhecida',
    fields,
    badge: semanticIdentityBadge(view),
    documentRole: view?.documentRole || 'desconhecido',
    answerKeyState: view?.answerKeyState || 'desconhecido',
    version: view?.versionNumber ? `Versão ${view.versionNumber}` : null,
    predecessorVersion: view?.predecessorVersionId || null,
    activeAnswerKeyVersion: view?.activeAnswerKeyVersion || null,
    showIdentityConfidence: view?.identityStatus === 'known',
    details: {
      evidence: view?.evidence || {},
      reason: view?.reason || view?.resolution || 'sem resolução registrada',
      algorithmVersion: view?.algorithmVersion || 'não informado',
    },
  };
}

function renderSemanticIdentityHistory(root, events) {
  const history = document.createElement('div');
  history.className = 'document-identity-history';
  const heading = document.createElement('strong');
  heading.textContent = 'Histórico semântico';
  history.append(heading);
  (events || []).forEach((event) => {
    const item = document.createElement('span');
    item.textContent = [
      `Ação: ${event.action || 'não informada'}`,
      `Ator: ${event.actor || 'não informado'}`,
      `Data: ${event.createdAt || 'não informada'}`,
      `Motivo: ${event.reason || 'não informado'}`,
      `Algoritmo: ${event.algorithmVersion || 'não informado'}`,
    ].join(' · ');
    history.append(item);
  });
  root.append(history);
}

function qwenPreviewPresentation(preview) {
  const counts = preview?.counts || {};
  const missing = Object.entries(counts.missingFields || {});
  const fieldLabels = {
    discipline: 'Disciplina', matter: 'Matéria', subject: 'Assunto', level: 'Nível',
  };
  const zeroReason = (counts.exclusionReasons || [])[0] || null;
  return {
    counts: [
      ['Questões brutas', counts.rawQuestions || 0],
      ['Com resposta oficial', counts.officialAnswered || 0],
      ['Prontas para classificar', counts.eligibleQuestions || 0],
      ['Unidades de classificação', counts.classificationUnits || 0],
      ['Cópias que herdam', counts.inheritedCopies || 0],
      ['Já completas', counts.alreadyComplete || 0],
      ['Resolvidas por regras', counts.deterministic || 0],
      ['Precisam do Qwen', counts.qwenRequired || 0],
    ],
    missing: counts.eligible > 0
      ? missing.map(([field, total]) => `${fieldLabels[field] || field}: ${total}`)
      : [],
    zeroReason: counts.eligible === 0 ? zeroReason : null,
    exclusions: counts.exclusionReasons || [],
  };
}

function questionStatePresentation(view) {
  const question = view?.question || {};
  const equivalence = view?.question_equivalence || {};
  const fieldLabels = {discipline: 'Disciplina', matter: 'Matéria', subject: 'Assunto', level: 'Nível'};
  const missingClassification = Object.keys(fieldLabels).filter((field) => !question[field]);
  const diagnosis = view?.answer_key_diagnosis || {};
  const answerContext = {
    examDocument: view?.answer_key_evidence?.examDocument || view?.filename || 'Não informado',
    answerKeyDocument: view?.answer_key_evidence?.linkedAnswerKeyDocument || 'Nenhum documento ligado',
  };
  const answer = question.answer_status === 'annulled'
    ? {state: 'Anulada', tone: 'attention', reason: diagnosis.explanation || 'O gabarito oficial anulou esta questão.', action: diagnosis.action || 'Nenhuma ação', context: answerContext}
    : question.answer_status === 'matched'
      ? {state: 'Com resposta oficial', tone: 'success', reason: diagnosis.explanation || 'A resposta foi vinculada ao gabarito oficial.', action: diagnosis.action || 'Nenhuma ação', context: answerContext}
      : {
          state: diagnosis.label || 'Diagnóstico pendente', tone: 'blocked',
          reason: diagnosis.explanation || 'O banco ainda não explica a ausência da resposta.',
          action: diagnosis.action || 'Revisar preparação', context: answerContext,
          details: view?.answer_key_evidence || {},
        };
  const preparation = equivalence.status === 'confirmed' && equivalence.canonicalQuestionId
    ? equivalence.isRepresentative
      ? {state: 'Principal', tone: 'success', reason: equivalence.hasStatementVariants ? 'Esta é a principal; questões realmente diferentes com o mesmo enunciado ficaram separadas.' : 'Esta é a cópia escolhida automaticamente para classificação e importação.', action: 'Nenhuma ação'}
      : {state: 'Cópia preservada', tone: 'success', reason: 'A classificação será herdada da principal e esta cópia não será importada.', action: 'Consultar a principal nos detalhes'}
    : equivalence.groupId
      ? {state: 'Equivalência pendente', tone: 'attention', reason: 'A questão foi agrupada, mas o grupo ainda precisa de confirmação.', action: 'Revisar grupo equivalente'}
      : view?.canonical_identity
        ? {state: 'Identidade pendente', tone: 'attention', reason: 'O documento foi reconhecido, mas a preparação das ocorrências ainda não terminou.', action: 'Executar preparação'}
        : {state: 'Bruta', tone: 'attention', reason: 'A questão foi extraída, mas ainda não entrou na preparação canônica.', action: 'Executar preparação'};
  const classification = missingClassification.length
    ? {state: 'Incompleta', tone: 'attention', reason: `Faltam ${missingClassification.map((field) => fieldLabels[field]).join(', ')}.`, action: question.answer_status === 'matched' ? 'Aplicar regras ou usar Qwen' : 'Resolver o gabarito primeiro'}
    : {state: 'Completa', tone: 'success', reason: 'Os campos editoriais necessários estão preenchidos.', action: 'Revisar conteúdo'};
  const importing = view?.importable
    ? {state: 'Pronta', tone: 'success', reason: 'Os requisitos mínimos de importação foram atendidos.', action: 'Revisar e exportar'}
    : {state: 'Bloqueada', tone: 'blocked', reason: view?.import_diagnosis?.issues?.[0]?.what || 'Existem requisitos de importação pendentes.', action: view?.import_diagnosis?.issues?.[0]?.how || 'Abra o diagnóstico abaixo'};
  return [
    {label: 'Gabarito', ...answer},
    {label: 'Preparação', ...preparation},
    {label: 'Classificação', ...classification},
    {label: 'Importação', ...importing},
  ];
}

if (typeof window !== 'undefined') {
  window.KADDesktopRenderers = {
    semanticIdentityBadge, semanticIdentityPresentation, renderSemanticIdentityHistory,
    qwenPreviewPresentation, questionStatePresentation,
  };
}
if (typeof module !== 'undefined' && module.exports) {
  module.exports = {
    semanticIdentityBadge, semanticIdentityPresentation, renderSemanticIdentityHistory,
    qwenPreviewPresentation, questionStatePresentation,
  };
}

if (typeof document !== 'undefined') {
const token = document.querySelector('meta[name="kad-desktop-token"]').content;
const emptyFilters = () => ({
  source_files: [], concursos: [], boards: [], years: [], roles: [], variants: [], levels: [],
  disciplines: [], subjects: [], topics: [], difficulties: [], statuses: [],
  answer_states: [], answer_diagnostics: [], readiness_states: [], block_reasons: [],
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
  batchClassificationPreview: null,
  localAIPreview: null,
  localAIStatus: null,
  localAIPolling: null,
  polling: null,
  activeSection: 'overview',
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
    importable: 'Importável', publication_ready: 'Pronta para publicação',
    unclassified: 'Não classificada', blocked: 'Bloqueada',
  }[status] || status;
}

function flagLabel(flag) {
  return {
    incomplete: 'Incompleta', without_explanation: 'Sem explicação',
    without_difficulty: 'Sem dificuldade', annulled: 'Anulada',
    without_answer: 'Sem gabarito', visual: 'Visual', missing_fields: 'Campos ausentes',
    low_confidence: 'Baixa confiança', duplicate: 'Duplicata',
  }[flag] || flag;
}

const facetDefinitions = [
  ['Gabarito oficial', [
    ['answer_states', 'Situação da resposta'], ['answer_diagnostics', 'Motivo da ausência'],
  ]],
  ['Origem', [
    ['source_files', 'PDF de origem'], ['concursos', 'Concurso'], ['boards', 'Banca'],
    ['years', 'Ano'], ['roles', 'Cargo'], ['variants', 'Variante'], ['levels', 'Nível'],
  ]],
  ['Conteúdo', [
    ['disciplines', 'Disciplina'], ['subjects', 'Matéria'], ['topics', 'Assunto'],
    ['difficulties', 'Dificuldade'],
  ]],
  ['Qualidade', [
    ['readiness_states', 'Importação no app'], ['block_reasons', 'Motivo do bloqueio'],
    ['quality_flags', 'Sinais de qualidade'],
  ]],
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
  renderOperationalOverview();
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
  const collecting = state.activeSection === 'collect';
  byId('editorial-view').hidden = collecting;
  byId('source-view').hidden = !collecting;
}

function renderOperationalOverview() {
  const summary = state.bootstrap.summary || {};
  const operational = state.bootstrap.operationalSummary || {};
  const preparation = state.bootstrap.preparationSummary || {};
  const config = state.bootstrap.config || {};
  byId('database-environment').textContent = config.environmentLabel || 'Banco operacional';
  byId('database-environment').className = `environment-badge ${config.environment || 'operational'}`;
  byId('database-path').textContent = config.databasePath || config.dataDirectory || '—';
  byId('overview-official').textContent = summary.answer_matched || 0;
  byId('overview-annulled').textContent = summary.answer_annulled || 0;
  byId('overview-unmatched').textContent = summary.answer_missing || 0;
  byId('overview-unclassified').textContent = summary.unclassified || 0;
  byId('prep-raw').textContent = operational.rawQuestions || 0;
  byId('prep-occurrences').textContent = operational.occurrences || 0;
  byId('prep-groups').textContent = operational.confirmedGroups || 0;
  byId('prep-canonical').textContent = operational.canonicalQuestions || 0;
  byId('prep-ready').textContent = preparation.qwenEligible || 0;
  byId('prep-duplicates').textContent = preparation.duplicateQuestions || 0;
  byId('prep-pending').textContent = preparation.pendingQuestions || 0;
  byId('canonical-empty-message').hidden = !(
    operational.rawQuestions > 0 && operational.canonicalQuestions === 0
  );
  const next = operational.nextAction || {};
  byId('next-action-title').textContent = next.title || 'Banco pronto para começar';
  byId('next-action-detail').textContent = next.detail || '';
  byId('next-action-button').textContent = next.action || 'Ver fluxo';
  byId('next-action-button').dataset.step = next.step || 'collect';
  byId('prepare-questions-open').disabled = (operational.rawQuestions || 0) === 0;
  renderPreparationReviews(preparation.reviews || []);
}

function renderPreparationReviews(reviews) {
  const root = byId('preparation-review-list');
  root.replaceChildren();
  if (!reviews.length) return;
  const heading = document.createElement('div');
  heading.className = 'preparation-review-heading';
  const title = document.createElement('strong');
  title.textContent = `${reviews.length} prova(s) precisam de revisão`;
  const copy = document.createElement('span');
  copy.textContent = 'Corrija os dados indicados e execute a preparação novamente.';
  heading.append(title, copy);
  root.append(heading);
  reviews.forEach((review) => {
    const item = document.createElement('article');
    const text = document.createElement('div');
    const name = document.createElement('strong');
    name.textContent = review.filename || 'Prova sem nome';
    const reason = document.createElement('span');
    reason.textContent = review.missingLabels?.length
      ? `Conferir: ${review.missingLabels.join(', ')}.`
      : review.reason;
    const context = document.createElement('small');
    context.textContent = `${review.questionCount || 0} questão(ões) · ${review.candidates?.length || 0} gabarito(s) candidato(s)`;
    text.append(name, reason, context);
    const button = document.createElement('button');
    button.type = 'button';
    button.className = 'button ghost';
    button.textContent = 'Revisar prova';
    button.disabled = !review.questionId;
    button.addEventListener('click', () => openQuestion(review.questionId));
    item.append(text, button);
    root.append(item);
  });
}

function renderPreparationPreview(preview) {
  const root = byId('preparation-preview');
  root.replaceChildren();
  const grid = document.createElement('div');
  grid.className = 'qwen-count-grid';
  [
    ['Questões coletadas', preview.rawQuestions || 0],
    ['Questões principais', preview.mainQuestions || preview.canonicalQuestions || 0],
    ['Cópias repetidas', preview.duplicateQuestions || 0],
    ['Separadas por conflito', preview.conflictQuestions || 0],
    ['Unidades para o Qwen', preview.qwenEligible || 0],
    ['Questões cobertas', preview.qwenEligibleQuestions || 0],
    ['Cópias que herdam', preview.qwenInheritedCopies || 0],
    ['Pendentes', preview.pendingQuestions || 0],
  ].forEach(([label, value]) => {
    const item = document.createElement('span');
    const number = document.createElement('strong');
    number.textContent = value;
    const caption = document.createElement('small');
    caption.textContent = label;
    item.append(number, caption);
    grid.append(item);
  });
  root.append(grid);
  const detail = document.createElement('p');
  detail.className = 'qwen-zero-reason';
  detail.textContent = preview.pendingCases
    ? `${preview.pendingCases} caso(s) continuarão na revisão por falta de evidência suficiente.`
    : 'Nenhum caso duvidoso foi encontrado.';
  root.append(detail);
}

async function openPreparation() {
  const button = byId('prepare-questions-open');
  button.disabled = true;
  try {
    const preview = await request('/api/preparation/preview', {method: 'POST', body: '{}'});
    renderPreparationPreview(preview);
    byId('preparation-submit').disabled = (preview.identifiedExams || 0) === 0;
    byId('preparation-dialog').showModal();
  } catch (error) {
    toast(error.message, 'error');
  } finally {
    button.disabled = false;
  }
}

async function runPreparation(event) {
  event.preventDefault();
  const button = byId('preparation-submit');
  button.disabled = true;
  try {
    const result = await request('/api/preparation/run', {method: 'POST', body: '{}'});
    byId('preparation-dialog').close();
    toast(`${result.qwenEligible || 0} questão(ões) ficaram prontas para classificação.`);
    await loadBootstrap({preserveQuery: false});
  } catch (error) {
    toast(error.message, 'error');
  } finally {
    button.disabled = false;
  }
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
    `${summary.answer_matched || 0} com resposta oficial · ${summary.answer_annulled || 0} anuladas · ${summary.answer_missing || 0} sem gabarito associado`;
  byId('metric-pending').textContent = summary.pending || 0;
  byId('metric-exceptions').textContent = summary.exception || 0;
  byId('metric-missing-answers').textContent = `${summary.answer_missing || 0} sem resposta oficial`;
  byId('metric-importable').textContent = summary.importable || 0;
  const activeStatus = state.filters.statuses.length === 1 ? state.filters.statuses[0] : null;
  byId('metric-card-pending').classList.toggle('active', activeStatus === 'pending');
  byId('metric-card-exceptions').classList.toggle('active', activeStatus === 'exception');
  byId('metric-card-importable').classList.toggle('active', activeStatus === 'importable');
  const activeAnswer = state.filters.answer_states.length === 1 ? state.filters.answer_states[0] : null;
  byId('metric-card-answer-official').classList.toggle('active', activeAnswer === 'official');
  byId('metric-card-answer-annulled').classList.toggle('active', activeAnswer === 'annulled');
  byId('metric-card-answer-missing').classList.toggle('active', activeAnswer === 'missing');
  renderAnswerDiagnosticSummary(summary.answer_key_diagnostics || {});
}

const answerStateLabels = {
  official: 'Com resposta oficial', annulled: 'Anulada', missing: 'Sem resposta associada',
};
const answerDiagnosticLabels = {
  answer_key_not_collected: 'Gabarito oficial não encontrado',
  answer_key_unlinked: 'Gabarito aguardando associação',
  question_missing_in_answer_key: 'Questão não localizada no gabarito',
  ambiguous_answer_key_association: 'Associação do gabarito em dúvida',
  answer_key_diagnosis_pending: 'Motivo ainda não identificado',
};

function renderAnswerDiagnosticSummary(counts) {
  const root = byId('answer-diagnostic-summary');
  root.replaceChildren();
  const intro = document.createElement('div');
  const heading = document.createElement('strong');
  heading.textContent = 'Por que faltam respostas?';
  const detail = document.createElement('span');
  detail.textContent = 'Cada questão aparece em um único motivo, conforme a evidência guardada.';
  intro.append(heading, detail);
  root.append(intro);
  Object.entries(answerDiagnosticLabels).forEach(([code, label]) => {
    const button = document.createElement('button');
    button.type = 'button';
    button.className = 'answer-diagnostic-button';
    const count = document.createElement('strong');
    count.textContent = counts[code] || 0;
    const copy = document.createElement('span');
    copy.textContent = label;
    button.append(count, copy);
    button.classList.toggle('active', state.filters.answer_diagnostics.length === 1 && state.filters.answer_diagnostics[0] === code);
    button.addEventListener('click', () => activateAnswerQueue('missing', code));
    root.append(button);
  });
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
  const activeAnswer = state.filters.answer_states.length === 1 ? state.filters.answer_states[0] : null;
  byId('records-kicker').textContent = activeAnswer === 'official' ? 'COM RESPOSTA OFICIAL'
    : activeAnswer === 'annulled' ? 'QUESTÕES ANULADAS'
      : activeAnswer === 'missing' ? 'SEM RESPOSTA ASSOCIADA'
        : activeStatus === 'pending' ? 'FILA DE REVISÃO'
    : activeStatus === 'exception' ? 'QUESTÕES EM EXCEÇÃO'
      : activeStatus === 'importable' ? 'IMPORTÁVEIS PARA O APP' : 'QUESTÕES ENCONTRADAS';
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
          : key === 'block_reasons' ? blockReasonLabel(option.value)
            : key === 'answer_states' ? (answerStateLabels[option.value] || option.value)
              : key === 'answer_diagnostics' ? (answerDiagnosticLabels[option.value] || option.value)
                : ['statuses', 'readiness_states'].includes(key)
              ? statusLabel(option.value) : String(option.value);
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
  const label = key === 'quality_flags' ? flagLabel(value)
    : key === 'block_reasons' ? blockReasonLabel(value)
      : key === 'answer_states' ? (answerStateLabels[value] || value)
        : key === 'answer_diagnostics' ? (answerDiagnosticLabels[value] || value)
      : ['statuses', 'readiness_states'].includes(key) ? statusLabel(value) : value;
  button.textContent = `${label} ×`;
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
    discipline.textContent = question.discipline || 'Não classificada';
    const topic = document.createElement('small');
    topic.textContent = [question.matter, question.subject].filter(Boolean).join(' · ') || 'Classificação pendente';
    classification.append(discipline, topic);

    const answer = document.createElement('span');
    answer.className = `answer-copy ${view.answer_key_state || 'missing'}`;
    const answerLabel = document.createElement('strong');
    const answerCode = view.answer_key_diagnosis?.diagnosticCode;
    answerLabel.textContent = view.answer_key_state === 'official' ? 'Resposta oficial encontrada'
      : view.answer_key_state === 'annulled' ? 'Questão anulada'
        : ['answer_key_unlinked', 'ambiguous_answer_key_association'].includes(answerCode)
          ? 'Associação pendente' : 'Sem resposta oficial';
    answer.append(answerLabel);
    if (view.answer_key_state === 'missing') {
      const answerReason = document.createElement('small');
      answerReason.className = 'answer-reason';
      answerReason.textContent = view.answer_key_diagnosis?.label || 'Motivo ainda não identificado';
      const answerExplanation = document.createElement('small');
      answerExplanation.textContent = view.answer_key_diagnosis?.explanation || 'A resposta oficial ainda não foi comprovada.';
      const answerAction = document.createElement('small');
      answerAction.textContent = `Próxima ação: ${view.answer_key_diagnosis?.action || 'Abrir detalhes para diagnóstico'}`;
      answer.append(answerReason, answerExplanation, answerAction);
    }

    const confidence = document.createElement('span');
    confidence.className = 'confidence';
    const hasClassification = Boolean(question.discipline || question.matter || question.subject);
    const percentage = Math.round((view.confidence || 0) * 100);
    confidence.append(document.createTextNode(hasClassification ? `${percentage}%` : 'Não classificada'));
    const bar = document.createElement('span');
    bar.className = 'confidence-bar';
    const fill = document.createElement('span');
    fill.style.width = `${hasClassification ? percentage : 0}%`;
    bar.append(fill);
    confidence.append(bar);

    const status = document.createElement('span');
    status.className = `status-pill ${view.status}`;
    status.textContent = view.importable ? 'Importável' : statusLabel(view.status);
    open.append(main, answer, classification, confidence, status);
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
  byId('batch-classification-open').disabled = selected === 0;
  const selectAll = byId('select-all-pending');
  selectAll.checked = pending.length > 0 && selected === pending.length;
  selectAll.indeterminate = selected > 0 && selected < pending.length;
}

function activateEditorialQueue(status) {
  state.activeSection = 'review';
  state.filters = emptyFilters();
  state.filters.statuses = [status];
  state.selectedQuestionIds.clear();
  byId('question-search').value = '';
  document.querySelectorAll('.rail-link').forEach((item) => item.classList.remove('active'));
  document.querySelector('.rail-link[data-section="review"]')?.classList.add('active');
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
    state.currentIdentity = await request(`/api/documents/${payload.question.document_id}/identity`);
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
  byId('edit-stage').value = view.metadata.stage || '';
  byId('edit-turn').value = view.metadata.turn || '';
  byId('edit-organization').value = question.organization || '';
  byId('edit-level').value = question.level || '';
  byId('edit-difficulty').value = question.difficulty || '';
  byId('edit-provider').value = view.metadata.provider || '';
  byId('edit-source-url').value = view.metadata.source_url || '';
  byId('edit-decision-notes').value = view.review_notes || '';
  renderAlternativeEditor(question.alternatives);
  renderCorrectAnswers(question.correct_answer);
  byId('edit-correct-answer').disabled = question.answer_status !== 'matched';
  renderReviewFlags();
  renderQuestionStates();
  renderEquivalenceDetails();
  renderImportDiagnosis();
  renderBatchCorrection();
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
  renderDocumentIdentity(
    byId('document-identity'),
    state.currentIdentity || view.documentIdentity,
  );
}

function renderDocumentIdentity(root, view) {
  root.replaceChildren();
  const presentation = semanticIdentityPresentation(view);
  const header = document.createElement('div');
  header.className = 'document-identity-header';
  const title = document.createElement('h3');
  title.textContent = presentation.identityLabel;
  const badge = document.createElement('span');
  badge.className = `document-identity-badge semantic-${view?.resolution || 'unknown'}`;
  badge.textContent = presentation.badge;
  header.append(title);
  if (presentation.badge) header.append(badge);
  root.append(header);

  if (presentation.fields.length) {
    const fields = document.createElement('div');
    fields.className = 'document-identity-fields';
    presentation.fields.forEach(([label, value]) => {
      const field = document.createElement('span');
      field.className = 'document-identity-field';
      field.textContent = `${label}: ${value}`;
      fields.append(field);
    });
    root.append(fields);
  }

  const meta = document.createElement('div');
  meta.className = 'document-identity-meta';
  [
    `Papel: ${presentation.documentRole}`,
    `Gabarito: ${presentation.answerKeyState}`,
    presentation.version,
    presentation.predecessorVersion && `Predecessora: ${presentation.predecessorVersion}`,
    presentation.activeAnswerKeyVersion && `Gabarito ativo: ${presentation.activeAnswerKeyVersion}`,
  ].filter(Boolean).forEach((value) => {
    const item = document.createElement('span');
    item.textContent = value;
    meta.append(item);
  });
  root.append(meta);

  const details = document.createElement('details');
  const summary = document.createElement('summary');
  summary.textContent = 'Evidências e resolução';
  const detailsBody = document.createElement('div');
  detailsBody.className = 'document-identity-details';
  [
    `Motivo: ${presentation.details.reason}`,
    `Algoritmo: ${presentation.details.algorithmVersion}`,
    `Evidências: ${JSON.stringify(presentation.details.evidence)}`,
  ].forEach((value) => {
    const item = document.createElement('span');
    item.textContent = value;
    detailsBody.append(item);
  });
  details.append(summary, detailsBody);
  root.append(details);
  renderSemanticIdentityHistory(root, view?.events || []);
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
    review_notes: [...(original.review_notes || [])],
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
      source: value === null ? 'unresolved' : 'human_review',
      reason: value === null ? 'Campo removido na revisão humana' : 'Valor confirmado pelo revisor',
    };
  });
  return classification;
}

function documentMetadata(question) {
  return {
    ...state.currentQuestion.metadata,
    provider: optional('edit-provider'), source_url: optional('edit-source-url'),
    variant: optional('edit-variant'),
    stage: optional('edit-stage'), turn: optional('edit-turn'),
    concurso: question.concurso, board: question.board, year: question.year,
    role: question.role, organization: question.organization, level: question.level,
    discipline: question.discipline, subject: question.matter, topic: question.subject,
    difficulty: question.difficulty,
  };
}

async function saveCurrentQuestion({silent = false} = {}) {
  const question = collectQuestion();
  await request(`/api/questions/${state.currentQuestion.id}`, {
    method: 'PUT',
    body: JSON.stringify({
      question, classification: humanClassification(question),
    }),
  });
  await request(`/api/documents/${state.currentQuestion.document_id}`, {
    method: 'PUT', body: JSON.stringify({metadata: documentMetadata(question)}),
  });
  if (!silent) toast('Alterações salvas e registradas na auditoria.');
  const refreshed = await request(`/api/questions/${state.currentQuestion.id}`);
  state.currentQuestion = refreshed.question;
  state.currentAudit = refreshed.audit;
  fillReviewForm();
}

async function decideCurrent(status) {
  try {
    const notes = optional('edit-decision-notes');
    if (['exception', 'rejected'].includes(status) && !notes) {
      throw new Error('Informe a justificativa para esta decisão.');
    }
    if (status !== 'pending') await saveCurrentQuestion({silent: true});
    await request(`/api/questions/${state.currentQuestion.id}/decision`, {
      method: 'POST',
      body: JSON.stringify({status, notes}),
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

async function openBatchClassification() {
  try {
    const preview = await request('/api/questions/classification-batch/preview', {
      method: 'POST',
      body: JSON.stringify({questionIds: [...state.selectedQuestionIds]}),
    });
    state.batchClassificationPreview = preview;
    byId('batch-classification-copy').textContent =
      `${preview.count} questão(ões) têm exatamente a mesma sugestão e evidência.`;
    const suggestion = preview.suggestion;
    byId('batch-classification-suggestion').textContent =
      `${suggestion.discipline} › ${suggestion.matter} › ${suggestion.subject}`;
    byId('batch-classification-dialog').showModal();
  } catch (error) { toast(error.message, 'error'); }
}

async function submitBatchClassification(event) {
  event.preventDefault();
  const preview = state.batchClassificationPreview;
  if (!preview) return;
  const button = byId('batch-classification-submit');
  button.disabled = true;
  try {
    const result = await request('/api/questions/classification-batch/confirm', {
      method: 'POST',
      body: JSON.stringify({
        questionIds: preview.questionIds,
        confirmationToken: preview.confirmationToken,
      }),
    });
    byId('batch-classification-dialog').close();
    state.batchClassificationPreview = null;
    state.selectedQuestionIds.clear();
    toast(`${result.updated} classificação(ões) confirmada(s) e auditada(s).`);
    await loadBootstrap({preserveQuery: true});
  } catch (error) { toast(error.message, 'error'); }
  finally { button.disabled = false; }
}

async function revertCurrentClassificationBatch() {
  const batchId = byId('revert-classification-batch').dataset.batchId;
  if (!batchId || !window.confirm('Corrigir esta decisão para todas as questões do lote?')) return;
  try {
    const result = await request(`/api/classification-batches/${batchId}/revert`, {
      method: 'POST', body: '{}',
    });
    toast(`${result.reverted} classificação(ões) restaurada(s), com auditoria.`);
    byId('review-dialog').close();
    await loadBootstrap({preserveQuery: true});
  } catch (error) { toast(error.message, 'error'); }
}

async function openExportPreview() {
  try {
    const preview = await request('/api/export/preview', {
      method: 'POST', body: JSON.stringify({filters: state.filters}),
    });
    byId('export-preview-summary').textContent =
      `${preview.included} de ${preview.selected} selecionada(s) entrarão no arquivo; ${preview.exceptions} ficarão no relatório de exceções.`;
    const root = byId('export-preview-list');
    root.replaceChildren();
    const answerSummary = document.createElement('strong');
    answerSummary.textContent = `Neste recorte: ${preview.answerKeySummary?.official || 0} com resposta oficial · ${preview.answerKeySummary?.annulled || 0} anuladas · ${preview.answerKeySummary?.missing || 0} sem resposta associada.`;
    root.append(answerSummary);
    Object.entries(preview.answerKeyDiagnostics || {}).forEach(([code, count]) => {
      const item = document.createElement('span');
      item.textContent = `${answerDiagnosticLabels[code] || code}: ${count}`;
      root.append(item);
    });
    preview.questions.slice(0, 100).forEach((question) => {
      const item = document.createElement('span');
      item.textContent = `Questão ${question.number} · ${question.discipline} · ${question.sourceDocument}`;
      root.append(item);
    });
    if (!preview.questions.length) {
      const item = document.createElement('span');
      item.textContent = 'Nenhuma questão atende hoje a todos os requisitos da exportação.';
      root.append(item);
    }
    byId('export-preview-submit').disabled = preview.included === 0;
    byId('export-preview-dialog').showModal();
  } catch (error) { toast(error.message, 'error'); }
}

async function exportCurrentFilter(event) {
  if (event) event.preventDefault();
  let outputPath = null;
  try {
    if (window.pywebview?.api) outputPath = await window.pywebview.api.choose_export_folder();
    else outputPath = window.prompt('Pasta de destino (deixe vazio para usar a pasta padrão):') || null;
    if (window.pywebview?.api && !outputPath) return;
    byId('export-preview-dialog').close();
    const result = await request('/api/export', {
      method: 'POST', body: JSON.stringify({filters: state.filters, outputPath}),
    });
    toast(`Exportação concluída: ${result.exported} válida(s), ${result.exceptions} exceção(ões). Pasta: ${result.directory}`);
    await loadBootstrap({preserveQuery: false});
  } catch (error) { toast(error.message, 'error'); }
}

function renderImportDiagnosis() {
  const root = byId('import-diagnosis');
  root.replaceChildren();
  const diagnosis = state.currentQuestion.import_diagnosis || {importable: false, issues: []};
  const heading = document.createElement('strong');
  heading.textContent = diagnosis.importable
    ? 'Importável para o app' : 'O que impede a importação';
  root.append(heading);
  if (diagnosis.importable) {
    const copy = document.createElement('span');
    copy.textContent = 'Resposta, alternativas, classificação e origem atendem ao contrato.';
    root.append(copy);
    return;
  }
  diagnosis.issues.forEach((issue) => {
    const card = document.createElement('article');
    const title = document.createElement('strong');
    title.textContent = issue.what;
    const why = document.createElement('span');
    why.textContent = `Por quê: ${issue.why}`;
    const how = document.createElement('span');
    how.textContent = `Como resolver: ${issue.how_to_resolve}`;
    const source = document.createElement('small');
    source.textContent = `Origem: ${issue.source_document}`;
    card.append(title, why, how, source);
    root.append(card);
  });
}

function renderBatchCorrection() {
  const button = byId('revert-classification-batch');
  button.hidden = true;
  delete button.dataset.batchId;
  const event = state.currentAudit[0];
  if (event?.action !== 'classification_batch_confirmed') return;
  try {
    const payload = JSON.parse(event.after_json || '{}');
    if (!payload.batchId) return;
    button.dataset.batchId = payload.batchId;
    button.hidden = false;
  } catch (_) { /* evento legado sem JSON estruturado */ }
}

function blockReasonLabel(reason) {
  return {
    missing_classification: 'Classificação ausente', missing_metadata: 'Metadados ausentes',
    missing_year: 'Ano ausente', missing_source_page: 'Página de origem ausente',
    invalid_statement: 'Enunciado inválido', missing_official_answer: 'Resposta oficial ausente',
    annulled_answer: 'Questão anulada', invalid_alternatives: 'Alternativas inválidas',
    visual_content: 'Conteúdo visual', unproved_origin: 'Origem não comprovada',
    unresolved_duplicate: 'Duplicata não resolvida', ambiguous_association: 'Associação ambígua',
    version_conflict: 'Conflito de versão',
  }[reason] || reason;
}

async function reclassifyCollection() {
  const button = byId('reclassify-open');
  button.disabled = true;
  try {
    const result = await request('/api/questions/reclassify', {
      method: 'POST', body: '{}',
    });
    const recovered = result.recovery?.fieldsRecovered || 0;
    toast(`Reclassificação concluída: ${result.changed} principal(is) alterada(s) de ${result.total}; ${recovered} campo(s) recuperado(s) das cópias. Taxonomia ${result.taxonomyVersion}.`);
    await loadBootstrap({preserveQuery: false});
  } catch (error) { toast(error.message, 'error'); }
  finally { button.disabled = false; }
}

function renderEquivalenceDetails() {
  const root = byId('equivalence-details');
  root.replaceChildren();
  const equivalence = state.currentQuestion?.question_equivalence;
  if (!equivalence?.groupId) {
    root.hidden = true;
    return;
  }
  root.hidden = false;
  const title = document.createElement('strong');
  title.textContent = equivalence.isRepresentative
    ? 'Esta é a cópia principal' : 'Esta é uma cópia repetida';
  const summary = document.createElement('span');
  const total = equivalence.occurrenceCount || equivalence.provenances?.length || 1;
  summary.textContent = `${total} cópia(s) preservada(s). Somente a principal será importada.`;
  root.append(title, summary);
  const list = document.createElement('div');
  list.className = 'equivalence-copy-list';
  (equivalence.provenances || []).forEach((copy) => {
    const item = document.createElement('small');
    const isMain = copy.occurrenceId === equivalence.representativeOccurrenceId;
    item.textContent = `${isMain ? 'Principal' : 'Cópia'} · ${copy.booklet || 'tipo não informado'} · questão ${copy.questionNumber} · ${copy.filename || 'documento preservado'}`;
    list.append(item);
  });
  root.append(list);
}

function activateAnswerQueue(answerState, diagnosticCode = null) {
  state.activeSection = 'review';
  state.filters = emptyFilters();
  state.filters.answer_states = [answerState];
  if (diagnosticCode) state.filters.answer_diagnostics = [diagnosticCode];
  state.selectedQuestionIds.clear();
  byId('question-search').value = '';
  document.querySelectorAll('.rail-link').forEach((item) => item.classList.remove('active'));
  document.querySelector('.rail-link[data-section="review"]')?.classList.add('active');
  renderSection();
  runQuery().then(() => {
    document.querySelector('.workbench')?.scrollIntoView({behavior: 'smooth', block: 'start'});
  }).catch((error) => toast(error.message, 'error'));
}

function renderQuestionStates() {
  const root = byId('question-state-groups');
  root.replaceChildren();
  questionStatePresentation(state.currentQuestion).forEach((item) => {
    const card = document.createElement('article');
    card.className = `question-state ${item.tone}`;
    const label = document.createElement('span');
    label.textContent = item.label;
    const stateLabel = document.createElement('strong');
    stateLabel.textContent = item.state;
    const reason = document.createElement('p');
    reason.textContent = item.reason;
    const action = document.createElement('small');
    action.textContent = `Próxima ação: ${item.action}`;
    card.append(label, stateLabel, reason, action);
    if (item.context) {
      const context = document.createElement('p');
      context.className = 'answer-document-context';
      context.textContent = `Prova: ${item.context.examDocument} · Gabarito relacionado: ${item.context.answerKeyDocument}`;
      card.append(context);
    }
    if (item.details && Object.values(item.details).some((value) => value !== null && value !== '' && (!Array.isArray(value) || value.length))) {
      const details = document.createElement('details');
      details.className = 'answer-technical-details';
      const summary = document.createElement('summary');
      summary.textContent = 'Detalhes para diagnóstico';
      details.append(summary);
      Object.entries(item.details).forEach(([key, value]) => {
        if (value === null || value === '' || (Array.isArray(value) && !value.length)) return;
        const row = document.createElement('span');
        row.textContent = `${key}: ${Array.isArray(value) ? value.join(', ') : value}`;
        details.append(row);
      });
      card.append(details);
    }
    root.append(card);
  });
}

function localAIStateLabel(value) {
  return {
    idle: 'Aguardando', starting: 'Verificando GPU', running: 'Em execução',
    pause_requested: 'Pausando com segurança', paused: 'Pausada',
    completed: 'Concluída', blocked: 'Bloqueada',
  }[value] || value;
}

function appendContractRow(root, label, value) {
  const term = document.createElement('dt');
  term.textContent = label;
  const detail = document.createElement('dd');
  detail.textContent = String(value ?? '—');
  root.append(term, detail);
}

function renderQwenPreview(preview) {
  const presentation = qwenPreviewPresentation(preview);
  const root = byId('qwen-classification-preview');
  root.replaceChildren();
  const grid = document.createElement('div');
  grid.className = 'qwen-count-grid';
  presentation.counts.forEach(([label, value]) => {
    const item = document.createElement('span');
    item.innerHTML = `<strong>${value}</strong><small>${label}</small>`;
    grid.append(item);
  });
  root.append(grid);
  if (presentation.zeroReason) {
    const reason = document.createElement('div');
    reason.className = 'qwen-zero-reason';
    reason.innerHTML = `<strong>${presentation.zeroReason.label}</strong><span>${presentation.zeroReason.action}</span>`;
    root.append(reason);
  } else if (presentation.missing.length) {
    const missing = document.createElement('p');
    missing.textContent = `Campos ausentes: ${presentation.missing.join(' · ')}`;
    root.append(missing);
  }
  if (presentation.exclusions.length) {
    const details = document.createElement('details');
    const summary = document.createElement('summary');
    summary.textContent = 'Ver motivos de exclusão';
    const list = document.createElement('ul');
    presentation.exclusions.forEach((item) => {
      const row = document.createElement('li');
      row.textContent = `${item.label}${item.count ? ` (${item.count})` : ''}: ${item.action}`;
      list.append(row);
    });
    details.append(summary, list);
    root.append(details);
  }
  const contract = byId('qwen-classification-contract');
  contract.replaceChildren();
  appendContractRow(contract, 'Modelo', preview.preflight.model);
  appendContractRow(contract, 'Digest', preview.preflight.digest);
  appendContractRow(contract, 'Quantização', preview.preflight.quantization);
  appendContractRow(contract, 'Endpoint', preview.preflight.endpoint);
  appendContractRow(contract, 'Ollama', preview.preflight.ollamaVersion);
  byId('qwen-classification-warning').textContent = preview.warning;
  byId('qwen-classification-submit').disabled = (preview.counts?.eligible || 0) === 0;
}

async function refreshQwenPreview() {
  const limit = Number(byId('qwen-classification-limit').value);
  if (!Number.isInteger(limit) || limit < 1 || limit > 250) {
    throw new Error('Escolha um limite entre 1 e 250.');
  }
  const button = byId('qwen-preview-refresh');
  const submit = byId('qwen-classification-submit');
  button.disabled = true;
  submit.disabled = true;
  try {
    const preview = await request('/api/local-ai/classification/preview', {
      method: 'POST', body: JSON.stringify({limit}),
    });
    state.localAIPreview = preview;
    renderQwenPreview(preview);
  } finally {
    button.disabled = false;
  }
}

async function openQwenClassification() {
  const button = byId('qwen-classify-open');
  button.disabled = true;
  try {
    await refreshQwenPreview();
    byId('qwen-classification-dialog').showModal();
  } catch (error) {
    const operational = state.bootstrap.operationalSummary || {};
    renderQwenPreview({
      counts: {
        rawQuestions: operational.rawQuestions || 0,
        canonicalQuestions: operational.canonicalQuestions || 0,
        eligible: 0,
        exclusionReasons: [{
          code: 'ollama_unavailable',
          label: 'Ollama ou modelo indisponível',
          count: 0,
          action: error.message,
        }],
      },
      preflight: {},
      warning: 'A classificação não pode começar enquanto o ambiente local não atender ao contrato aprovado.',
    });
    byId('qwen-classification-dialog').showModal();
  } finally {
    button.disabled = false;
  }
}

async function startQwenClassification(event) {
  event.preventDefault();
  const preview = state.localAIPreview;
  const limit = Number(byId('qwen-classification-limit').value);
  if (!preview || preview.limit !== limit) {
    return toast('Atualize a prévia depois de mudar o limite.', 'error');
  }
  const button = byId('qwen-classification-submit');
  button.disabled = true;
  try {
    const status = await request('/api/local-ai/classification/start', {
      method: 'POST',
      body: JSON.stringify({confirmationToken: preview.confirmationToken, limit}),
    });
    state.localAIStatus = status;
    state.localAIPreview = null;
    byId('qwen-classification-dialog').close();
    renderLocalAIStatus();
    scheduleLocalAIPoll();
    toast(status.state === 'completed'
      ? 'Não havia classificação pendente neste lote.'
      : 'Classificação local iniciada. O aplicativo pode continuar sendo usado.');
  } catch (error) {
    toast(error.message, 'error');
  } finally {
    button.disabled = false;
  }
}

function renderLocalAIStatus() {
  const status = state.localAIStatus;
  const strip = byId('qwen-job-strip');
  if (!status || status.state === 'idle') {
    strip.hidden = true;
    return;
  }
  strip.hidden = false;
  byId('qwen-job-state').textContent = [
    localAIStateLabel(status.state),
    status.pauseReason,
    `${status.model} · ${status.quantization}`,
  ].filter(Boolean).join(' · ');
  const metrics = byId('qwen-job-metrics');
  metrics.replaceChildren();
  [
    `Processadas ${status.processed}/${status.target}`,
    `Restantes ${status.remaining}`,
    `Chamadas IA ${status.aiCalls}`,
    `Sugestões aceitas ${status.acceptedSuggestions}`,
    `Para revisão ${status.reviewRequired}`,
    `Falhas ${status.failures}`,
  ].forEach((copy) => {
    const item = document.createElement('span');
    item.textContent = copy;
    metrics.append(item);
  });
  byId('qwen-job-pause').hidden = !['starting', 'running'].includes(status.state);
  byId('qwen-job-resume').hidden = !['paused', 'blocked'].includes(status.state);
}

async function refreshLocalAIStatus() {
  const previous = state.localAIStatus?.state;
  state.localAIStatus = await request('/api/local-ai/classification/status');
  renderLocalAIStatus();
  scheduleLocalAIPoll();
  if (previous && previous !== state.localAIStatus.state && state.localAIStatus.state === 'completed') {
    await loadBootstrap({preserveQuery: false});
  }
}

function scheduleLocalAIPoll() {
  clearTimeout(state.localAIPolling);
  if (['starting', 'running', 'pause_requested'].includes(state.localAIStatus?.state)) {
    state.localAIPolling = setTimeout(() => refreshLocalAIStatus().catch(() => {}), 1000);
  }
}

async function localAIAction(action) {
  const runId = state.localAIStatus?.runId;
  if (!runId) return;
  try {
    state.localAIStatus = await request(`/api/local-ai/classification/${runId}/${action}`, {
      method: 'POST', body: '{}',
    });
    renderLocalAIStatus();
    scheduleLocalAIPoll();
    toast(action === 'pause'
      ? 'Pausa solicitada; a questão atual terminará antes da parada.'
      : 'Classificação local retomada no mesmo ponto.');
  } catch (error) {
    toast(error.message, 'error');
  }
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
byId('reclassify-open').addEventListener('click', reclassifyCollection);
byId('qwen-classify-open').addEventListener('click', openQwenClassification);
byId('prepare-questions-open').addEventListener('click', openPreparation);
byId('preparation-form').addEventListener('submit', runPreparation);
byId('qwen-preview-refresh').addEventListener('click', () => refreshQwenPreview().catch((error) => toast(error.message, 'error')));
byId('qwen-classification-limit').addEventListener('input', () => {
  state.localAIPreview = null;
  byId('qwen-classification-submit').disabled = true;
  byId('qwen-classification-preview').textContent = 'Atualize a prévia para este limite.';
});
byId('qwen-classification-form').addEventListener('submit', startQwenClassification);
byId('qwen-job-pause').addEventListener('click', () => localAIAction('pause'));
byId('qwen-job-resume').addEventListener('click', () => localAIAction('resume'));
byId('export-open').addEventListener('click', openExportPreview);
byId('export-summary-open').addEventListener('click', openExportPreview);
byId('metric-card-pending').addEventListener('click', () => activateEditorialQueue('pending'));
byId('metric-card-exceptions').addEventListener('click', () => activateEditorialQueue('exception'));
byId('metric-card-importable').addEventListener('click', () => activateEditorialQueue('importable'));
byId('metric-card-answer-official').addEventListener('click', () => activateAnswerQueue('official'));
byId('metric-card-answer-annulled').addEventListener('click', () => activateAnswerQueue('annulled'));
byId('metric-card-answer-missing').addEventListener('click', () => activateAnswerQueue('missing'));
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
byId('revert-classification-batch').addEventListener('click', revertCurrentClassificationBatch);
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
byId('batch-classification-open').addEventListener('click', openBatchClassification);
byId('batch-classification-form').addEventListener('submit', submitBatchClassification);
byId('export-preview-form').addEventListener('submit', exportCurrentFilter);
document.querySelectorAll('.rail-link').forEach((button) => {
  button.addEventListener('click', async () => {
    document.querySelectorAll('.rail-link').forEach((item) => item.classList.remove('active'));
    button.classList.add('active');
    const section = button.dataset.section;
    state.activeSection = section;
    state.selectedQuestionIds.clear();
    renderSection();
    if (section === 'collect') return;
    state.filters.statuses = section === 'review'
      ? ['pending', 'exception']
      : section === 'export' ? ['importable', 'exported'] : [];
    await runQuery();
    const target = {
      prepare: 'preparation-overview', complete: 'completion-overview',
      review: 'review-workbench', export: 'export-overview', overview: 'main',
    }[section];
    byId(target)?.scrollIntoView({behavior: 'smooth', block: 'start'});
  });
});
document.querySelectorAll('[data-section-jump]').forEach((button) => {
  button.addEventListener('click', () => {
    document.querySelector(`.rail-link[data-section="${button.dataset.sectionJump}"]`)?.click();
  });
});

byId('copy-database-path').addEventListener('click', async () => {
  try {
    await navigator.clipboard.writeText(state.bootstrap.config.databasePath);
    toast('Caminho do banco copiado.');
  } catch (_) {
    toast('Não foi possível copiar. Selecione o caminho exibido.', 'error');
  }
});
byId('next-action-button').addEventListener('click', () => {
  const step = byId('next-action-button').dataset.step || 'collect';
  document.querySelector(`.rail-link[data-section="${step}"]`)?.click();
});

applyCapacityProfile();
Promise.all([loadBootstrap(), refreshLocalAIStatus()]).catch((error) => toast(error.message, 'error'));
}
