const token = document.querySelector('meta[name="kad-review-token"]').content;
const state = { payload: null, selected: null, filter: 'all' };
const byId = (id) => document.getElementById(id);

async function request(path, options = {}) {
  const response = await fetch(path, {
    ...options,
    headers: {
      'Content-Type': 'application/json',
      'X-KAD-Review-Token': token,
      ...(options.headers || {}),
    },
  });
  const payload = await response.json();
  if (!response.ok) throw new Error(payload.error || 'Falha na operação local.');
  return payload;
}

function optionalValue(id) {
  const value = byId(id).value.trim();
  return value || null;
}

function currentSession() {
  return state.payload.session;
}

function decisionFor(number) {
  return currentSession().decisions.find((item) => item.question_number === number);
}

function questionFor(number) {
  return currentSession().batch.questions.find((item) => item.number === number);
}

function statusLabel(status) {
  return { pending: 'Pendente', approved: 'Aprovada', rejected: 'Rejeitada' }[status];
}

function showNotice(message, kind = 'info') {
  const notice = byId('notice');
  notice.textContent = message;
  notice.className = `notice ${kind}`;
  notice.hidden = false;
  window.clearTimeout(showNotice.timeout);
  showNotice.timeout = window.setTimeout(() => { notice.hidden = true; }, 6000);
}

function renderSummary() {
  const summary = state.payload.summary;
  byId('summary-total').textContent = currentSession().batch.questions.length;
  byId('summary-pending').textContent = summary.pending;
  byId('summary-approved').textContent = summary.approved;
  byId('summary-rejected').textContent = summary.rejected;
}

function renderBatch() {
  const batch = currentSession().batch;
  const source = batch.source_document;
  byId('batch-title').textContent = source.title;
  byId('batch-meta').textContent = `${source.source_name} · ${batch.model} · ${batch.batch_id}`;
  const sourceLink = byId('source-link');
  sourceLink.hidden = !state.payload.source_available;
  sourceLink.setAttribute('aria-disabled', String(!state.payload.source_available));
}

function renderQuestionList() {
  const list = byId('question-list');
  list.replaceChildren();
  const questions = currentSession().batch.questions.filter((question) => {
    const status = decisionFor(question.number).status;
    return state.filter === 'all' || status === state.filter;
  });
  if (!questions.length) {
    const empty = document.createElement('p');
    empty.className = 'list-empty';
    empty.textContent = 'Nenhuma questão neste filtro.';
    list.append(empty);
    return;
  }
  questions.forEach((question) => {
    const decision = decisionFor(question.number);
    const button = document.createElement('button');
    button.type = 'button';
    button.className = `question-item ${decision.status}`;
    if (state.selected === question.number) button.classList.add('selected');
    const index = document.createElement('span');
    index.className = 'question-index';
    index.textContent = String(question.number).padStart(2, '0');
    const copy = document.createElement('span');
    const title = document.createElement('strong');
    title.textContent = question.subject || question.matter || `Questão ${question.number}`;
    const excerpt = document.createElement('small');
    excerpt.textContent = question.statement;
    copy.append(title, excerpt);
    const dot = document.createElement('span');
    dot.className = `status-dot ${decision.status}`;
    dot.title = statusLabel(decision.status);
    button.append(index, copy, dot);
    button.addEventListener('click', () => selectQuestion(question.number));
    list.append(button);
  });
}

function selectQuestion(number) {
  state.selected = number;
  renderQuestionList();
  renderEditor();
}

function renderAlternatives(alternatives) {
  const container = byId('alternatives');
  container.replaceChildren();
  alternatives.forEach((alternative) => {
    const row = document.createElement('div');
    row.className = 'alternative-row';
    const letter = document.createElement('input');
    letter.className = 'alternative-letter';
    letter.maxLength = 1;
    letter.value = alternative.letter;
    letter.setAttribute('aria-label', 'Letra da alternativa');
    letter.addEventListener('input', () => {
      letter.value = letter.value.toUpperCase().replace(/[^A-H]/g, '');
      renderCorrectAnswers();
    });
    const text = document.createElement('textarea');
    text.rows = 2;
    text.value = alternative.text;
    text.setAttribute('aria-label', `Texto da alternativa ${alternative.letter}`);
    const remove = document.createElement('button');
    remove.type = 'button';
    remove.className = 'remove-alternative';
    remove.textContent = '×';
    remove.title = 'Remover alternativa';
    remove.addEventListener('click', () => {
      if (container.children.length <= 2) {
        showNotice('Uma questão precisa ter pelo menos duas alternativas.', 'error');
        return;
      }
      row.remove();
      renderCorrectAnswers();
    });
    row.append(letter, text, remove);
    container.append(row);
  });
  renderCorrectAnswers();
}

function alternativeValues() {
  return [...byId('alternatives').querySelectorAll('.alternative-row')].map((row) => ({
    letter: row.querySelector('.alternative-letter').value.trim().toUpperCase(),
    text: row.querySelector('textarea').value.trim(),
  }));
}

function renderCorrectAnswers() {
  const select = byId('correct-answer');
  const previous = select.value;
  select.replaceChildren();
  const blank = document.createElement('option');
  blank.value = '';
  blank.textContent = 'Selecione';
  select.append(blank);
  alternativeValues().filter((item) => item.letter).forEach((item) => {
    const option = document.createElement('option');
    option.value = item.letter;
    option.textContent = item.letter;
    select.append(option);
  });
  select.value = [...select.options].some((option) => option.value === previous) ? previous : '';
  select.disabled = byId('answer-status').value !== 'matched';
}

function renderEditor() {
  const question = questionFor(state.selected);
  if (!question) return;
  const decision = decisionFor(question.number);
  byId('empty-editor').hidden = true;
  byId('question-form').hidden = false;
  byId('question-number').textContent = question.number;
  byId('question-heading').textContent = question.subject || question.matter || 'Questão sem classificação';
  const pill = byId('decision-pill');
  pill.textContent = statusLabel(decision.status);
  pill.className = `decision-pill ${decision.status}`;
  byId('statement').value = question.statement;
  byId('matter').value = question.matter || '';
  byId('subject').value = question.subject || '';
  byId('board').value = question.board || '';
  byId('organization').value = question.organization || '';
  byId('role').value = question.role || '';
  byId('year').value = question.year || '';
  byId('source-pages').value = question.source_pages.join(', ');
  byId('review-notes').value = question.review_notes.join('\n');
  byId('answer-status').value = question.answer_status;
  renderAlternatives(question.alternatives);
  byId('correct-answer').value = question.correct_answer || '';
  byId('decision-notes').value = decision.notes || '';
}

function readQuestion() {
  const original = questionFor(state.selected);
  const sourcePages = byId('source-pages').value
    .split(',')
    .map((value) => Number(value.trim()))
    .filter((value) => Number.isInteger(value) && value > 0);
  const answerStatus = byId('answer-status').value;
  return {
    ...original,
    statement: byId('statement').value.trim(),
    alternatives: alternativeValues(),
    matter: optionalValue('matter'),
    subject: optionalValue('subject'),
    board: optionalValue('board'),
    organization: optionalValue('organization'),
    role: optionalValue('role'),
    year: byId('year').value ? Number(byId('year').value) : null,
    source_pages: [...new Set(sourcePages)],
    answer_status: answerStatus,
    correct_answer: answerStatus === 'matched' ? (byId('correct-answer').value || null) : null,
    review_notes: byId('review-notes').value.split('\n').map((item) => item.trim()).filter(Boolean),
  };
}

async function saveQuestion({ quiet = false } = {}) {
  if (!state.selected) throw new Error('Selecione uma questão.');
  state.payload = await request(`/api/questions/${state.selected}`, {
    method: 'PUT',
    body: JSON.stringify(readQuestion()),
  });
  renderSummary();
  renderQuestionList();
  renderEditor();
  if (!quiet) showNotice('Alterações salvas. A decisão voltou para pendente.', 'success');
}

async function decide(status) {
  const reviewer = byId('reviewer').value.trim();
  if (!reviewer) throw new Error('Informe o revisor responsável no topo da página.');
  const notes = byId('decision-notes').value.trim();
  if (status === 'rejected' && !notes) throw new Error('Informe a justificativa da rejeição.');
  await saveQuestion({ quiet: true });
  state.payload = await request(`/api/questions/${state.selected}/decision`, {
    method: 'POST',
    body: JSON.stringify({ status, reviewer, notes }),
  });
  renderSummary();
  renderQuestionList();
  renderEditor();
  showNotice(status === 'approved' ? 'Questão aprovada.' : 'Questão rejeitada.', 'success');
}

async function exportApproved() {
  const reviewer = byId('reviewer').value.trim();
  if (!reviewer) throw new Error('Informe o revisor responsável antes de exportar.');
  const result = await request('/api/export', {
    method: 'POST',
    body: JSON.stringify({ reviewer, notes: byId('batch-notes').value.trim() }),
  });
  showNotice(`Lote exportado com ${result.question_count} questões para ${result.output_path}`, 'success');
}

function guarded(action) {
  return async () => {
    try {
      await action();
    } catch (error) {
      showNotice(error.message || String(error), 'error');
    }
  };
}

byId('add-alternative').addEventListener('click', () => {
  const values = alternativeValues();
  if (values.length >= 8) return showNotice('O limite é de oito alternativas.', 'error');
  const used = new Set(values.map((item) => item.letter));
  const letter = 'ABCDEFGH'.split('').find((candidate) => !used.has(candidate)) || '';
  renderAlternatives([...values, { letter, text: '' }]);
});
byId('answer-status').addEventListener('change', renderCorrectAnswers);
byId('save-button').addEventListener('click', guarded(() => saveQuestion()));
byId('approve-button').addEventListener('click', guarded(() => decide('approved')));
byId('reject-button').addEventListener('click', guarded(() => decide('rejected')));
byId('export-button').addEventListener('click', guarded(exportApproved));
document.querySelectorAll('.filter').forEach((button) => {
  button.addEventListener('click', () => {
    document.querySelectorAll('.filter').forEach((item) => item.classList.remove('active'));
    button.classList.add('active');
    state.filter = button.dataset.filter;
    renderQuestionList();
  });
});

request('/api/session')
  .then((payload) => {
    state.payload = payload;
    renderSummary();
    renderBatch();
    renderQuestionList();
    const first = currentSession().batch.questions[0];
    if (first) selectQuestion(first.number);
  })
  .catch((error) => showNotice(error.message || String(error), 'error'));
