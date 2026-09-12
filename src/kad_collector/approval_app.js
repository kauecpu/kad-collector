const token = document.querySelector('meta[name="kad-approval-token"]').content;
const workspace = document.querySelector('#workspace');
const pipeline = document.querySelector('#pipeline');
const dialog = document.querySelector('#question-dialog');
const detail = document.querySelector('#question-detail');
const toast = document.querySelector('#toast');
let state;
let view = 'sample';
const filters = { search: '', board: '', year: '', method: '', blocker: '', discipline: '' };

const esc = (value) => String(value ?? '').replace(/[&<>'"]/g, (char) => ({
  '&': '&amp;', '<': '&lt;', '>': '&gt;', "'": '&#39;', '"': '&quot;'
}[char]));

async function api(path, options = {}) {
  const response = await fetch(path, {
    ...options,
    headers: { 'Content-Type': 'application/json', 'X-KAD-Approval-Token': token, ...(options.headers || {}) }
  });
  const payload = await response.json();
  if (!response.ok) throw new Error(payload.error || 'A operação falhou.');
  return payload;
}

function notify(message) {
  toast.textContent = message;
  toast.classList.add('show');
  setTimeout(() => toast.classList.remove('show'), 2600);
}

function metric(label, value, kind = '') {
  return `<div class="metric ${kind}"><span>${esc(label)}</span><strong>${esc(value)}</strong></div>`;
}

function renderPipeline() {
  const s = state.summary;
  const eligible = s.auto_ready + s.audit_sample + s.approved_for_staging;
  pipeline.innerHTML = [
    metric('Ocorrências', s.raw_occurrences),
    metric('Elegíveis', eligible),
    metric('Amostra humana', s.audit_sample, 'audit'),
    metric('Exceções', s.needs_review),
    metric('Quarentena', s.quarantined),
    metric('Liberadas p/ staging', s.approved_for_staging, 'approved')
  ].join('');
}

function stateBadge(value) { return `<span class="badge ${esc(value)}">${esc(value)}</span>`; }

function questionTable(items) {
  if (!items.length) return '<div class="empty">Nenhuma questão nesta fila.</div>';
  return `<div class="table-wrap"><table><thead><tr><th>Questão</th><th>Grupo</th><th>Contexto</th><th>Estado</th><th>Bloqueios</th></tr></thead><tbody>${items.map((item) => `
    <tr>
      <td><button class="link-button question" data-id="${esc(item.stableId)}">#${esc(item.number)}</button></td>
      <td><code>${esc(item.groupId.slice(-8))}</code></td>
      <td>${esc(item.board)} · ${esc(item.contest)} · ${esc(item.role)} · ${esc(item.year)}</td>
      <td>${stateBadge(item.state)}</td>
      <td>${esc((item.blockers || []).join(', ') || '—')}</td>
    </tr>`).join('')}</tbody></table></div>`;
}

function renderSample() {
  const pending = state.sample.filter((item) => !item.decision || item.decision.status === 'deferred').length;
  workspace.innerHTML = `<div class="section-head"><div><h2>Amostra de auditoria</h2><p>${pending} decisões ainda faltam. Cada decisão mede a qualidade do grupo inteiro.</p></div></div>${questionTable(state.sample)}`;
}

function renderExceptions() {
  const cards = Object.entries(state.exceptions).map(([reason, count]) => `<div class="reason"><span>${esc(reason)}</span><strong>${count}</strong></div>`).join('');
  const values = (field) => [...new Set(state.reviewQueue.map((item) => item[field]).filter(Boolean))].sort();
  const options = (field, label, source = field) => `<select data-filter="${field}"><option value="">${label}: todos</option>${values(source).map((value) => `<option value="${esc(value)}" ${filters[field] === String(value) ? 'selected' : ''}>${esc(value)}</option>`).join('')}</select>`;
  const blockers = [...new Set(state.reviewQueue.flatMap((item) => item.blockers || []))].sort();
  const filtered = state.reviewQueue.filter((item) => {
    const haystack = [item.number, item.board, item.organization, item.contest, item.role, item.discipline, item.matter, item.subject].join(' ').toLowerCase();
    return (!filters.search || haystack.includes(filters.search.toLowerCase()))
      && (!filters.board || item.board === filters.board)
      && (!filters.year || String(item.year) === filters.year)
      && (!filters.method || item.classificationMethod === filters.method)
      && (!filters.discipline || item.discipline === filters.discipline)
      && (!filters.blocker || (item.blockers || []).includes(filters.blocker));
  });
  workspace.innerHTML = `<div class="section-head"><div><h2>Fila de exceções</h2><p>Só aparecem casos que precisam de correção ou isolamento.</p></div></div><div class="reason-grid">${cards || '<div class="empty">Nenhuma exceção.</div>'}</div><div class="filter-bar"><input data-filter="search" value="${esc(filters.search)}" placeholder="Buscar questão, órgão, concurso ou cargo">${options('board', 'Banca')}${options('year', 'Ano')}${options('method', 'Método', 'classificationMethod')}${options('discipline', 'Disciplina')}<select data-filter="blocker"><option value="">Bloqueio: todos</option>${blockers.map((value) => `<option value="${esc(value)}" ${filters.blocker === value ? 'selected' : ''}>${esc(value)}</option>`).join('')}</select><strong>${filtered.length} de ${state.reviewQueue.length}</strong></div>${questionTable(filtered)}`;
}

function precision(value) { return value == null ? '—' : `${(value * 100).toFixed(1)}%`; }

function renderGroups() {
  workspace.innerHTML = `<div class="section-head"><div><h2>Grupos editoriais</h2><p>Aprovação só é aceita quando a amostra completa atinge todos os limites.</p></div><button id="export" class="action">Gerar pacote de staging</button></div>
  <div class="table-wrap"><table><thead><tr><th>Grupo</th><th>Questões</th><th>Auditoria</th><th>Precisões E / G / T</th><th>Estado</th><th>Ações</th></tr></thead><tbody>${state.groups.map((group) => `<tr>
    <td><code>${esc(group.group_id.slice(-10))}</code><br><small>${esc(group.key.board)} · ${esc(group.key.contest)} · ${esc(group.key.role)}</small></td>
    <td>${group.question_ids.length}</td><td>${group.reviewed_items}/${group.required_items}</td>
    <td>${precision(group.structural_precision)} / ${precision(group.answer_precision)} / ${precision(group.taxonomy_precision)}</td>
    <td>${stateBadge(group.status)}<br><small>${esc(group.blockers.join(', '))}</small></td>
    <td><button class="link-button group-approve" data-id="${esc(group.group_id)}">Validar aprovação</button><br><button class="link-button group-block" data-id="${esc(group.group_id)}">Bloquear</button><br><button class="link-button group-reprocess" data-id="${esc(group.group_id)}">Reprocessar</button></td>
  </tr>`).join('')}</tbody></table></div>`;
}

function render() {
  renderPipeline();
  if (view === 'sample') renderSample();
  if (view === 'exceptions') renderExceptions();
  if (view === 'groups') renderGroups();
}

async function load() { state = await api('/api/state'); render(); }

async function openQuestion(id) {
  const payload = await api(`/api/questions/${encodeURIComponent(id)}`);
  const q = payload.question;
  const a = payload.approval;
  detail.innerHTML = `<div class="detail"><p class="eyebrow">${esc(a.state)} · ${esc(a.group_id)}</p><h2>Questão ${q.number}</h2><p class="metadata">${esc(q.board)} · ${esc(q.organization)} · ${esc(q.concurso)} · ${esc(q.role)} · ${esc(q.year)}</p>
    <p><a href="${esc(payload.examUrl)}" target="_blank" rel="noreferrer">Abrir prova oficial</a> · <a href="${esc(payload.answerKeyUrl)}" target="_blank" rel="noreferrer">Abrir gabarito oficial</a></p>
    <div class="statement">${esc(q.statement)}</div>${q.alternatives.map((x) => `<div class="alternative"><strong>${esc(x.letter)}</strong><span>${esc(x.text)}</span></div>`).join('')}
    <p><strong>Resposta:</strong> ${esc(q.correct_answer)} · <strong>Taxonomia:</strong> ${esc(q.discipline)} › ${esc(q.matter)} › ${esc(q.subject)}</p>
    <div class="dimension-grid">${Object.entries(a.dimensions).map(([name, d]) => `<div class="dimension ${d.passed ? '' : 'fail'}"><strong>${esc(name)}</strong><br>${Math.round(d.score * 100)}% · ${d.passed ? 'passou' : 'bloqueou'}<br><small>${esc(d.evidence.join(' · '))}</small></div>`).join('')}</div>
    <details><summary>Corrigir item</summary><form class="correction-form audit-form" data-id="${esc(id)}"><label>Enunciado<textarea name="statement" rows="5">${esc(q.statement)}</textarea></label><label>Alternativas — uma por linha<textarea name="alternatives" rows="5">${esc(q.alternatives.map((x) => `${x.letter}: ${x.text}`).join('\n'))}</textarea></label><div class="checks"><label>Resposta<input name="correct" value="${esc(q.correct_answer)}" maxlength="1"></label><label>Disciplina<input name="discipline" value="${esc(q.discipline)}"></label><label>Matéria<input name="matter" value="${esc(q.matter)}"></label><label>Assunto<input name="subject" value="${esc(q.subject)}"></label></div><button class="action secondary">Salvar e reavaliar</button></form></details>
    <form class="audit-form" data-id="${esc(id)}"><label>Revisor<input name="reviewer" type="text" required minlength="2" placeholder="Seu nome"></label><div class="checks"><label><input name="structural" type="checkbox" checked> Estrutura correta</label><label><input name="answer" type="checkbox" checked> Gabarito correto</label><label><input name="taxonomy" type="checkbox" checked> Taxonomia correta</label><label><input name="critical" type="checkbox"> É erro crítico</label></div><label>Observação<textarea name="notes" rows="3"></textarea></label><div class="form-actions"><button class="action" name="decision" value="approved">Aprovar amostra</button><button class="action danger" name="decision" value="rejected">Rejeitar amostra</button><button class="action secondary" name="decision" value="deferred">Adiar</button></div></form></div>`;
  dialog.dataset.question = JSON.stringify(q);
  dialog.showModal();
}

document.querySelector('.close').addEventListener('click', () => dialog.close());
document.querySelectorAll('.tab').forEach((button) => button.addEventListener('click', () => {
  document.querySelectorAll('.tab').forEach((item) => item.classList.remove('active'));
  button.classList.add('active'); view = button.dataset.view; render();
}));

workspace.addEventListener('click', async (event) => {
  const button = event.target.closest('button'); if (!button) return;
  try {
    if (button.classList.contains('question')) return openQuestion(button.dataset.id);
    if (button.id === 'export') { const result = await api('/api/export', { method: 'POST', body: '{}' }); notify(`${result.manifest.questions} questões gravadas no pacote draft.`); return load(); }
    if (button.classList.contains('group-approve')) { await api(`/api/groups/${button.dataset.id}/approve`, { method: 'POST', body: '{}' }); notify('Critérios do grupo confirmados.'); return load(); }
    if (button.classList.contains('group-reprocess')) { await api(`/api/groups/${button.dataset.id}/reprocess`, { method: 'POST', body: '{}' }); notify('Grupo preparado para nova auditoria.'); return load(); }
    if (button.classList.contains('group-block')) {
      const reviewer = prompt('Identificador do revisor:'); const reason = prompt('Motivo do bloqueio:');
      if (reviewer && reason) { await api(`/api/groups/${button.dataset.id}/block`, { method: 'POST', body: JSON.stringify({ reviewer, reason }) }); notify('Grupo bloqueado.'); return load(); }
    }
  } catch (error) { notify(error.message); }
});

workspace.addEventListener('input', (event) => {
  const control = event.target.closest('[data-filter]');
  if (!control) return;
  filters[control.dataset.filter] = control.value;
  renderExceptions();
  const replacement = workspace.querySelector(`[data-filter="${control.dataset.filter}"]`);
  if (replacement && control.tagName === 'INPUT') {
    replacement.focus(); replacement.setSelectionRange(replacement.value.length, replacement.value.length);
  }
});

detail.addEventListener('submit', async (event) => {
  event.preventDefault(); const form = event.target;
  if (form.classList.contains('correction-form')) {
    try {
      const question = JSON.parse(dialog.dataset.question);
      question.statement = form.statement.value;
      question.correct_answer = form.correct.value.trim().toUpperCase();
      question.discipline = form.discipline.value.trim();
      question.matter = form.matter.value.trim();
      question.subject = form.subject.value.trim();
      question.alternatives = form.alternatives.value.split('\n').filter(Boolean).map((line) => {
        const match = line.match(/^\s*([A-H])\s*:\s*(.+)$/i);
        if (!match) throw new Error(`Alternativa inválida: ${line}`);
        return { letter: match[1].toUpperCase(), text: match[2].trim() };
      });
      await api(`/api/questions/${encodeURIComponent(form.dataset.id)}`, { method: 'PUT', body: JSON.stringify({ question }) });
      dialog.close(); notify('Correção salva e critérios recalculados.'); await load();
    } catch (error) { notify(error.message); }
    return;
  }
  const clicked = event.submitter.value;
  const critical = form.critical?.checked ? ['erro_critico_confirmado'] : [];
  try {
    await api(`/api/questions/${encodeURIComponent(form.dataset.id)}/decision`, { method: 'POST', body: JSON.stringify({ status: clicked, reviewer: form.reviewer.value, structuralCorrect: clicked === 'deferred' ? null : form.structural.checked, answerCorrect: clicked === 'deferred' ? null : form.answer.checked, taxonomyCorrect: clicked === 'deferred' ? null : form.taxonomy.checked, criticalErrors: critical, notes: form.notes.value }) });
    dialog.close(); notify('Decisão registrada.'); await load();
  } catch (error) { notify(error.message); }
});

load().catch((error) => { workspace.innerHTML = `<div class="empty">${esc(error.message)}</div>`; });
