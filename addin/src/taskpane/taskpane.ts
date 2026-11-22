/// <reference types="office-js" />
/*
 * Боковая панель Word-Add-in-Copilot-Public (ТЗ 5.3-5.5).
 *
 * Правила работы с документом (ТЗ 5.4):
 * - текст уходит на обработку только по явному нажатию;
 * - документ целиком — только после подтверждения с объёмом текста;
 * - автоматическое внесение возможно только отдельным явно выбранным действием;
 * - все изменения выполняет Word в режиме записи исправлений от текущего пользователя.
 * Ответ модели выводится только через textContent — разметка из ответа не исполняется.
 */

type InputKind = "selection" | "paragraph" | "document";

interface ActionDescriptor {
  id: string;
  label: string;
  description: string;
  input: InputKind;
  resultType: "card" | "review" | "checklist" | "file" | "text";
  expectedDuration: "short" | "long";
  requiresConfirmation: boolean;
  requiresInstruction: boolean;
  autoApply: boolean;
  available: boolean;
  problem: string;
}

interface ActionContext {
  contractType: string;
  ourRole: string;
  counterpartyIdentifier: string;
  counterpartyCheck: string;
  lawyerInstruction: string;
}

interface ActionPayload {
  text: string;
  context: ActionContext;
  documentName: string;
  templateId?: string;
}

interface TypicalForm {
  id: string;
  name: string;
  folder: string;
}

interface ClauseReviewResponse {
  clauseType: string;
  riskLevel: string;
  issue: string;
  recommendation: string;
  proposedWording: string;
  counterpartyComment: string;
  needMoreContext: boolean;
  contextNeeded: string[];
  lawyerNote: string;
}

interface ReviewFinding {
  id: string;
  clauseReference: string;
  sourceText: string;
  clauseType: string;
  riskLevel: string;
  issue: string;
  recommendation: string;
  proposedWording: string;
  counterpartyComment: string;
  lawyerNote: string;
}

interface ContractReviewResponse {
  summary: string;
  overallRecommendation: string;
  findings: ReviewFinding[];
  businessQuestions: string[];
  lawyerNote: string;
}

type ChecklistStatus = "ok" | "issue" | "not_applicable" | "template_skip" | "needs_review";

interface ChecklistFinding {
  checkId: string;
  checkName: string;
  severity: "critical" | "advisory";
  status: ChecklistStatus;
  description: string;
  issue: string;
  recommendation: string;
}

interface ContractChecklistResponse {
  isTemplate: boolean;
  contractTypes: string[];
  counterpartyType: "legal_entity" | "individual" | "unknown";
  briefDescription: string;
  findings: ChecklistFinding[];
}

interface AutoApplySummary {
  applied: number;
  commented: number;
  skipped: number;
  manualEdits: number;
  authors: string[];
  verifiedChanges: number;
  verifiedComments: number;
}

interface CounterpartyCheckResponse {
  identifier: string;
  source: "DaData";
  lookupStatus: "найдено" | "не найдено" | "недоступно";
  company: {
    fullName: string;
    shortName: string;
    inn: string;
    kpp: string;
    ogrn: string;
    status: string;
    legalAddress: string;
    management: string;
  };
  summary: string;
  lawyerNote: string;
}

type ActionResult =
  | { type: "card"; card: ClauseReviewResponse | CounterpartyCheckResponse; engine: string }
  | { type: "review"; review: ContractReviewResponse; engine: string; template?: TypicalForm }
  | { type: "checklist"; checklist: ContractChecklistResponse; engine: string }
  | { type: "text"; text: string; engine: string };

interface TaskError {
  code: string;
  message: string;
  raw?: string;
  details?: string[];
}

type TaskState = "queued" | "running" | "done" | "failed" | "cancelled";

interface TaskStatus {
  taskId: string;
  actionId: string;
  state: TaskState;
  progress: string;
  result: ActionResult | null;
  error: TaskError | null;
  message?: string;
  durationSeconds: number;
  usage: TokenUsage;
}

interface TokenUsage {
  inputTokens: number;
  cachedInputTokens: number;
  outputTokens: number;
  reasoningTokens: number;
  totalTokens: number;
  costUsd?: number;
  estimated: boolean;
}

interface RunHandle {
  status: TaskStatus;
  background: boolean;
}

interface Health {
  engine: string;
  engineAvailable: boolean;
  engineVersion: string;
  engineMessage: string;
  projectValid: boolean;
  projectMessage: string;
  actionsError: string;
  dadataConfigured: boolean;
}

interface PrivacyPreview {
  inputChars: number;
  outputChars: number;
  redactions: number;
  headerRemoved: boolean;
  requisitesRemoved: boolean;
  identifiers: string[];
  safeToSend: boolean;
  warning: string;
}

type RelatedDocumentRole = "main_contract" | "amendment" | "appendix" | "reference" | "other";
type RelatedDocumentStatus = "processing" | "ready" | "error";

interface RelatedDocument {
  id: string;
  name: string;
  role: RelatedDocumentRole;
  roleLabel: string;
  included: boolean;
  status: RelatedDocumentStatus;
  sizeBytes: number;
  charCount: number;
  backend: string;
  pages: number | null;
  warnings: string[];
  error: string;
}

const MSG = {
  hostDown: "Приложение не запущено. Запустите Word-Add-in-Copilot-Public из меню «Пуск»",
  noSelection: "Выделите пункт договора",
  emptyDocument: "Документ пуст",
  notInWord: "Действие доступно только в Word",
};

const FINAL: TaskState[] = ["done", "failed", "cancelled"];
const POLL_MS = 2000;
type ContextFieldKey = "contractType" | "ourRole";
const CONTEXT_KEYS: ContextFieldKey[] = ["contractType", "ourRole"];
const ENGINE_NAMES: Record<string, string> = { openrouter: "OpenRouter", codex: "Codex", claude: "Claude Code", "local-dadata": "DaData (локально)" };
const DOCUMENT_ROLES: Array<[RelatedDocumentRole, string]> = [
  ["main_contract", "Основной договор"],
  ["amendment", "Дополнительное соглашение"],
  ["appendix", "Приложение или спецификация"],
  ["reference", "Справочный материал"],
  ["other", "Иной документ"],
];
const BINDING_WARNING_PREFIX = "Автопривязка Word недоступна:";

let sessionToken = "";
const SESSION_KEY = "wac-public:session";
const DOCUMENT_SCOPE_SETTING = "wac:document-scope";

const state = {
  actions: [] as ActionDescriptor[],
  inWord: false,
  documentWritable: false,
  hasSelection: false,
  busy: false,
  taskId: "",
  analyzedText: "",
  docKey: "untitled",
  counterpartyCheck: null as CounterpartyCheckResponse | null,
  pendingCounterpartyIdentifier: "",
  relatedDocuments: [] as RelatedDocument[],
  uploadingDocuments: false,
  dadataConfigured: false,
  instructionActionId: "",
  templateRequest: 0,
};

class ApiError extends Error {
  constructor(public code: string, message: string, public raw = "") {
    super(message);
  }
}

// ---------- DOM ----------

function $(id: string): HTMLElement {
  const el = document.getElementById(id);
  if (!el) throw new Error(`#${id} not found`);
  return el;
}

function el<K extends keyof HTMLElementTagNameMap>(tag: K, text = "", className = ""): HTMLElementTagNameMap[K] {
  const node = document.createElement(tag);
  if (text) node.textContent = text;
  if (className) node.className = className;
  return node;
}

function button(label: string, onClick: () => void, opts: { primary?: boolean; disabled?: boolean; title?: string } = {}): HTMLButtonElement {
  const b = el("button", label, opts.primary ? "primary" : "");
  b.disabled = !!opts.disabled;
  if (opts.title) b.title = opts.title;
  b.addEventListener("click", onClick);
  return b;
}

let toastTimer = 0;
function toast(text: string): void {
  const t = $("toast");
  t.textContent = text;
  t.hidden = false;
  window.clearTimeout(toastTimer);
  toastTimer = window.setTimeout(() => (t.hidden = true), 2500);
}

function banner(text: string): void {
  const b = $("banner");
  b.textContent = text;
  b.hidden = !text;
}

// ---------- API ----------

async function api<T>(path: string, init: RequestInit = {}): Promise<T> {
  let response: Response;
  try {
    const headers = new Headers(init.headers);
    if (sessionToken) headers.set("X-Session-Token", sessionToken);
    headers.set("X-Document-Scope", state.docKey);
    if (init.body && !(init.body instanceof FormData) && !headers.has("Content-Type")) {
      headers.set("Content-Type", "application/json");
    }
    response = await fetch(path, {
      ...init,
      headers,
    });
  } catch {
    throw new ApiError("host_unreachable", MSG.hostDown);
  }
  const body = await response.json().catch(() => null);
  if (!response.ok) {
    const err = body?.error;
    if (response.status === 401 && err?.code === "token_required") {
      sessionToken = "";
      try { window.sessionStorage.removeItem(SESSION_KEY); } catch { /* WebView может запрещать хранилище */ }
      $("workspace").hidden = true;
      $("pairing").hidden = false;
    }
    throw new ApiError(err?.code ?? `http_${response.status}`, err?.message ?? `Ошибка ${response.status}`, err?.raw ?? "");
  }
  return body as T;
}

async function loadActions(): Promise<ActionDescriptor[]> {
  return api<ActionDescriptor[]>("/actions");
}

async function runAction(actionId: string, payload: ActionPayload): Promise<RunHandle> {
  const status = await api<TaskStatus>("/run", { method: "POST", body: JSON.stringify({ actionId, ...payload }) });
  return { status, background: !FINAL.includes(status.state) };
}

async function privacyPreview(text: string, wholeDocument: boolean): Promise<PrivacyPreview> {
  return api<PrivacyPreview>("/privacy/preview", {
    method: "POST",
    body: JSON.stringify({ text, wholeDocument }),
  });
}

async function pollTask(taskId: string): Promise<TaskStatus> {
  return api<TaskStatus>(`/task/${encodeURIComponent(taskId)}`);
}

// ---------- связанные документы ----------

function formatBytes(value: number): string {
  if (value < 1024) return `${value} Б`;
  if (value < 1024 * 1024) return `${Math.ceil(value / 1024)} КБ`;
  return `${(value / 1024 / 1024).toLocaleString("ru-RU", { maximumFractionDigits: 1 })} МБ`;
}

function documentStatusText(item: RelatedDocument): string {
  if (item.status === "error") return `Ошибка извлечения: ${item.error || "неизвестная ошибка"}`;
  if (item.status === "processing") return "Извлекаю текст…";
  const details = [formatBytes(item.sizeBytes), `${item.charCount.toLocaleString("ru-RU")} знаков`];
  if (item.pages !== null) details.push(`${item.pages} стр.`);
  if (item.backend) details.push(item.backend);
  return `Готово · ${details.join(" · ")}`;
}

function renderRelatedDocuments(): void {
  const box = $("related-documents");
  box.replaceChildren();
  const ready = state.relatedDocuments.filter((item) => item.included && item.status === "ready").length;
  $("related-summary").textContent = state.uploadingDocuments
    ? "Извлекаю текст из выбранных документов…"
    : state.relatedDocuments.length
      ? `В комплекте: ${state.relatedDocuments.length}; включено в анализ: ${ready}`
      : "Документы не добавлены.";
  ($("related-add") as HTMLButtonElement).disabled = state.uploadingDocuments;

  for (const item of state.relatedDocuments) {
    const card = el("article", "", `related-document related-${item.status}`);
    const head = el("div", "", "related-document-head");
    const include = el("input") as HTMLInputElement;
    include.type = "checkbox";
    include.checked = item.included;
    include.disabled = item.status !== "ready";
    include.title = item.status === "ready" ? "Включить в следующий анализ" : "Текст документа недоступен";
    include.setAttribute("aria-label", `Включить ${item.name} в анализ`);
    include.addEventListener("change", () => void updateRelatedDocument(item.id, { included: include.checked }));
    head.append(include, el("strong", item.name));
    card.appendChild(head);

    const role = el("select") as HTMLSelectElement;
    role.setAttribute("aria-label", `Роль документа ${item.name}`);
    for (const [value, label] of DOCUMENT_ROLES) {
      const option = el("option", label) as HTMLOptionElement;
      option.value = value;
      option.selected = value === item.role;
      role.appendChild(option);
    }
    role.addEventListener("change", () => void updateRelatedDocument(item.id, { role: role.value as RelatedDocumentRole }));
    card.appendChild(role);
    card.appendChild(el("div", documentStatusText(item), item.status === "error" ? "related-error" : "hint"));
    for (const warning of item.warnings) card.appendChild(el("div", warning, "related-warning"));
    card.appendChild(button("Убрать из комплекта", () => void removeRelatedDocument(item)));
    box.appendChild(card);
  }
}

async function loadRelatedDocuments(): Promise<void> {
  state.relatedDocuments = await api<RelatedDocument[]>("/documents");
  renderRelatedDocuments();
}

async function updateRelatedDocument(id: string, patch: { role?: RelatedDocumentRole; included?: boolean }): Promise<void> {
  try {
    const updated = await api<RelatedDocument>(`/documents/${encodeURIComponent(id)}`, {
      method: "PATCH",
      body: JSON.stringify(patch),
    });
    state.relatedDocuments = state.relatedDocuments.map((item) => item.id === id ? updated : item);
  } catch (err) {
    toast((err as Error).message);
    await loadRelatedDocuments().catch(() => undefined);
  }
  renderRelatedDocuments();
}

async function removeRelatedDocument(item: RelatedDocument): Promise<void> {
  if (!window.confirm(`Убрать «${item.name}» из текущего комплекта? Временная копия и извлечённый текст будут удалены.`)) return;
  try {
    await api<{ removed: string }>(`/documents/${encodeURIComponent(item.id)}`, { method: "DELETE" });
    state.relatedDocuments = state.relatedDocuments.filter((candidate) => candidate.id !== item.id);
    renderRelatedDocuments();
  } catch (err) {
    toast((err as Error).message);
  }
}

async function uploadRelatedDocuments(files: FileList): Promise<void> {
  if (!files.length || state.uploadingDocuments) return;
  state.uploadingDocuments = true;
  renderRelatedDocuments();
  const failures: string[] = [];
  try {
    for (const file of Array.from(files)) {
      try {
        const form = new FormData();
        form.append("file", file, file.name);
        form.append("role", "reference");
        const created = await api<RelatedDocument>("/documents", { method: "POST", body: form });
        const index = state.relatedDocuments.findIndex((item) => item.id === created.id);
        if (index >= 0) state.relatedDocuments[index] = created;
        else state.relatedDocuments.push(created);
        renderRelatedDocuments();
      } catch (err) {
        failures.push(`${file.name}: ${(err as Error).message}`);
      }
    }
  } finally {
    state.uploadingDocuments = false;
    ($("related-files") as HTMLInputElement).value = "";
    renderRelatedDocuments();
  }
  if (failures.length) toast(failures.slice(0, 2).join("; "));
}

// ---------- документ ----------

async function getSelectedText(): Promise<string> {
  return Word.run(async (ctx) => {
    const range = ctx.document.getSelection();
    range.load("text");
    await ctx.sync();
    return range.text;
  });
}

async function getCurrentParagraphText(): Promise<string> {
  return Word.run(async (ctx) => {
    const paragraph = ctx.document.getSelection().paragraphs.getFirst();
    paragraph.load("text");
    await ctx.sync();
    return paragraph.text;
  });
}

async function getWholeDocumentText(): Promise<string> {
  return Word.run(async (ctx) => {
    const body = ctx.document.body;
    body.load("text");
    await ctx.sync();
    return body.text;
  });
}

async function textFor(input: InputKind): Promise<string> {
  if (!state.inWord) return (document.getElementById("debug-text") as HTMLTextAreaElement).value;
  if (input === "selection") return getSelectedText();
  if (input === "paragraph") return getCurrentParagraphText();
  return getWholeDocumentText();
}

function normalize(text: string): string {
  return text.replace(/\s+/g, " ").trim();
}

function documentReadOnlyMessage(): string {
  return "Документ открыт только для чтения. Нажмите «Разрешить редактирование» или сохраните редактируемую копию";
}

function requireDocumentEditable(): void {
  if (!state.inWord) throw new Error(MSG.notInWord);
  if (!state.documentWritable) throw new Error(documentReadOnlyMessage());
}

function wordMutationMessage(err: unknown): string {
  const error = err as { code?: string; message?: string };
  if (error.code === "AccessDenied" || error.code === "GeneralException") {
    return "Word не разрешил изменить документ. Проверьте режим «только чтение», защиту документа и блокировку исправлений";
  }
  return error.message || "Word не разрешил изменить документ";
}

async function insertProposedWording(text: string): Promise<void> {
  requireDocumentEditable();
  requireWordReviewApi();
  const current = await getSelectedText();
  if (normalize(current) && normalize(current) !== normalize(state.analyzedText)) {
    const ok = await askConfirm("Выделение изменилось после анализа. Заменить текущее выделение предлагаемой редакцией?", "Заменить");
    if (!ok) return;
  }
  await Word.run(async (ctx) => {
    ctx.document.changeTrackingMode = Word.ChangeTrackingMode.trackAll;
    ctx.document.getSelection().insertText(text, Word.InsertLocation.replace);
    await ctx.sync();
  });
  toast("Редакция вставлена");
}

function requireWordReviewApi(): void {
  if (!Office.context.requirements.isSetSupported("WordApi", "1.4")) {
    throw new Error("Эта версия Word не поддерживает рецензирование из надстройки");
  }
}

function requireWordTrackedChangesApi(): void {
  if (!Office.context.requirements.isSetSupported("WordApi", "1.6")) {
    throw new Error("Эта версия Word не позволяет проверить автора внесённых исправлений. Обновите Microsoft Word");
  }
}

function findingComment(finding: ReviewFinding): string {
  return finding.counterpartyComment || `${finding.issue}\n${finding.recommendation}`;
}

const WORD_TEXT_BREAK = /[\r\n\v\f\u0007\u2028\u2029]/;

function sourceBindingIssue(sourceText: string): string {
  if (!sourceText.trim()) return "Нет точной привязки к тексту документа";
  if (sourceText.length > 255 || WORD_TEXT_BREAK.test(sourceText)) {
    return "Цитата слишком длинная или содержит разрыв строки для точного поиска в Word";
  }
  return "";
}

function leadingClauseNumber(text: string): string {
  const match = text.trimStart().match(/^(\d+(?:\.\d+)*)([.)])?(?=\s|$)/);
  if (!match || (!match[2] && !match[1].includes("."))) return "";
  return match[1];
}

function findingEditIssue(finding: ReviewFinding): string {
  if (!finding.proposedWording.trim()) return "";
  if (WORD_TEXT_BREAK.test(finding.proposedWording)) {
    return "Редакция содержит несколько строк. Выделите в Word весь текст, который нужно заменить";
  }
  if (leadingClauseNumber(finding.sourceText) !== leadingClauseNumber(finding.proposedWording)) {
    return "Редакция меняет нумерацию. Выделите в Word весь текст, который нужно заменить";
  }
  return "";
}

function prepareReplacementCheck(range: Word.Range, finding: ReviewFinding): () => string {
  const issue = findingEditIssue(finding);
  if (issue || !finding.proposedWording.trim()) return () => issue;
  const current = range.getReviewedText(Word.ChangeTrackingVersion.current);
  const paragraph = leadingClauseNumber(finding.sourceText)
    ? range.paragraphs.getFirst().getReviewedText(Word.ChangeTrackingVersion.current)
    : null;
  return () => {
    if (current.value !== finding.sourceText) {
      return "Текущий текст Word отличается от исходной цитаты. Проверьте исправления или выделите нужный текст вручную";
    }
    if (paragraph && paragraph.value.trim() !== current.value.trim()) {
      return "Цитата занимает только часть нумерованного абзаца. Выделите в Word весь текст, который нужно заменить";
    }
    return "";
  };
}

function wordAuthorMessage(authors: string[]): string {
  const unique = Array.from(new Set(authors.map((name) => name.trim()).filter(Boolean)));
  return unique.length ? ` Автор Word: ${unique.join(", ")}.` : " Word не вернул имя автора.";
}

async function applyReviewAutomatically(result: ContractReviewResponse): Promise<AutoApplySummary> {
  requireDocumentEditable();
  requireWordReviewApi();
  requireWordTrackedChangesApi();

  const targets = result.findings.filter((finding) => !sourceBindingIssue(finding.sourceText));
  const skipped = result.findings.length - targets.length;
  if (!targets.length) return { applied: 0, commented: 0, skipped, manualEdits: 0, authors: [], verifiedChanges: 0, verifiedComments: 0 };

  try {
    return await Word.run(async (ctx) => {
      const prepared = targets.map((finding) => {
        const matches = ctx.document.body.search(finding.sourceText, { matchCase: true, matchWholeWord: false });
        matches.load("items");
        return { finding, matches };
      });
      await ctx.sync();

      for (const { finding, matches } of prepared) {
        if (matches.items.length === 0) {
          throw new Error(`Фрагмент для ${finding.id} не найден. Повторите анализ после изменений документа`);
        }
        if (matches.items.length > 1) {
          throw new Error(`Фрагмент для ${finding.id} встречается ${matches.items.length} раз. Автоматическое внесение остановлено`);
        }
      }

      const edits = prepared.map(({ finding, matches }) => ({
        finding,
        range: matches.items[0],
        check: prepareReplacementCheck(matches.items[0], finding),
      }));
      await ctx.sync();
      ctx.document.changeTrackingMode = Word.ChangeTrackingMode.trackAll;
      const comments: Word.Comment[] = [];
      const changes: Word.TrackedChangeCollection[] = [];
      let applied = 0;
      let commented = 0;
      let manualEdits = 0;

      for (const { finding, range: originalRange, check } of edits) {
        let range = originalRange;
        const issue = check();
        if (issue) manualEdits += 1;
        if (finding.proposedWording.trim() && !issue) {
          range = range.insertText(finding.proposedWording, Word.InsertLocation.replace);
          const tracked = range.getTrackedChanges();
          tracked.load("items/author");
          changes.push(tracked);
          applied += 1;
        }
        const commentText = findingComment(finding);
        if (commentText) {
          const comment = range.insertComment(commentText);
          comment.load("authorName");
          comments.push(comment);
          commented += 1;
        }
      }
      await ctx.sync();

      const authors = new Set<string>();
      let verifiedComments = 0;
      for (const comment of comments) {
        if (comment.authorName.trim()) {
          authors.add(comment.authorName.trim());
          verifiedComments += 1;
        }
      }
      let verifiedChanges = 0;
      for (const collection of changes) {
        verifiedChanges += collection.items.length;
        for (const change of collection.items) {
          if (change.author.trim()) authors.add(change.author.trim());
        }
      }
      return { applied, commented, skipped, manualEdits, authors: Array.from(authors), verifiedChanges, verifiedComments };
    });
  } catch (err) {
    throw new Error(wordMutationMessage(err));
  }
}

async function uniqueSourceRange(ctx: Word.RequestContext, sourceText: string): Promise<Word.Range> {
  const issue = sourceBindingIssue(sourceText);
  if (issue) throw new Error(issue);
  const matches = ctx.document.body.search(sourceText, { matchCase: true, matchWholeWord: false });
  matches.load("items");
  await ctx.sync();
  if (matches.items.length === 0) {
    throw new Error("Исходный фрагмент не найден в текущем документе. Повторите анализ после изменений");
  }
  if (matches.items.length > 1) {
    throw new Error(`Фрагмент встречается в документе ${matches.items.length} раз. Автоматическое действие остановлено`);
  }
  return matches.items[0];
}

async function withUniqueSourceRange(
  sourceText: string,
  action: (ctx: Word.RequestContext, range: Word.Range) => void,
): Promise<void> {
  if (!state.inWord) throw new Error(MSG.notInWord);
  await Word.run(async (ctx) => {
    const range = await uniqueSourceRange(ctx, sourceText);
    action(ctx, range);
    await ctx.sync();
  });
}

async function navigateToFinding(finding: ReviewFinding): Promise<void> {
  await withUniqueSourceRange(finding.sourceText, (_ctx, range) => range.select());
  toast("Пункт выделен в документе");
}

async function applyFinding(finding: ReviewFinding): Promise<void> {
  requireDocumentEditable();
  requireWordReviewApi();
  requireWordTrackedChangesApi();
  if (!finding.proposedWording.trim()) throw new Error("Для замечания не предложена новая редакция");
  const authors = await Word.run(async (ctx) => {
    const range = await uniqueSourceRange(ctx, finding.sourceText);
    const check = prepareReplacementCheck(range, finding);
    await ctx.sync();
    const issue = check();
    if (issue) throw new Error(issue);
    ctx.document.changeTrackingMode = Word.ChangeTrackingMode.trackAll;
    const inserted = range.insertText(finding.proposedWording, Word.InsertLocation.replace);
    const tracked = inserted.getTrackedChanges();
    tracked.load("items/author");
    await ctx.sync();
    return tracked.items.map((change) => change.author);
  });
  toast(`Редакция внесена в режиме рецензирования.${wordAuthorMessage(authors)}`);
}

async function applyFindingToSelection(finding: ReviewFinding): Promise<void> {
  requireDocumentEditable();
  requireWordReviewApi();
  requireWordTrackedChangesApi();
  if (state.busy) return;
  if (!finding.proposedWording.trim()) throw new Error("Для замечания не предложена новая редакция");
  await Word.run(async (ctx) => {
    const selected = ctx.document.getSelection();
    const original = selected.getReviewedText(Word.ChangeTrackingVersion.current);
    await ctx.sync();
    if (!original.value.trim()) throw new Error("Сначала выделите в Word текст, который нужно заменить");
    const preview = original.value.length > 240
      ? `${original.value.slice(0, 120)} … ${original.value.slice(-120)}`
      : original.value;
    const confirmation = askConfirm(
      `«${finding.id}»: заменить выделенный текст (${original.value.length} знаков) редакцией из карточки? «${preview}»`,
      "Заменить выделение",
    );
    $("confirm").scrollIntoView({ block: "nearest" });
    if (!await confirmation) return;
    const current = ctx.document.getSelection();
    const relation = selected.compareLocationWith(current);
    const currentText = current.getReviewedText(Word.ChangeTrackingVersion.current);
    await ctx.sync();
    if (relation.value !== Word.LocationRelation.equal || currentText.value !== original.value) {
      throw new Error("Выделение изменилось. Проверьте его и снова нажмите «Заменить выделение»");
    }
    ctx.document.changeTrackingMode = Word.ChangeTrackingMode.trackAll;
    const inserted = current.insertText(finding.proposedWording, Word.InsertLocation.replace);
    const tracked = inserted.getTrackedChanges();
    tracked.load("items/author");
    await ctx.sync();
    toast(`Выделение заменено в режиме рецензирования.${wordAuthorMessage(tracked.items.map((change) => change.author))}`);
  });
}

async function commentFinding(finding: ReviewFinding): Promise<void> {
  requireDocumentEditable();
  requireWordReviewApi();
  const text = findingComment(finding);
  const author = await Word.run(async (ctx) => {
    const range = await uniqueSourceRange(ctx, finding.sourceText);
    const comment = range.insertComment(text);
    comment.load("authorName");
    await ctx.sync();
    return comment.authorName;
  });
  toast(`Примечание добавлено к пункту.${wordAuthorMessage([author])}`);
}

async function addWordComment(text: string): Promise<void> {
  requireDocumentEditable();
  requireWordReviewApi();
  const author = await Word.run(async (ctx) => {
    const comment = ctx.document.getSelection().insertComment(text);
    comment.load("authorName");
    await ctx.sync();
    return comment.authorName;
  });
  toast(`Примечание добавлено.${wordAuthorMessage([author])}`);
}

async function copyToClipboard(text: string): Promise<void> {
  try {
    await navigator.clipboard.writeText(text);
  } catch {
    // WebView внутри Word может не давать доступ к Clipboard API — запасной путь.
    const area = el("textarea");
    area.value = text;
    area.style.position = "fixed";
    area.style.opacity = "0";
    document.body.appendChild(area);
    area.select();
    document.execCommand("copy");
    area.remove();
  }
  toast("Скопировано");
}

async function documentName(): Promise<string> {
  const url = state.inWord ? Office.context.document.url ?? "" : "";
  return url.split(/[\\/]/).pop() ?? "";
}

// ---------- контекст (на документ, только локально) ----------

async function hashKey(text: string): Promise<string> {
  const digest = await crypto.subtle.digest("SHA-256", new TextEncoder().encode(text));
  return Array.from(new Uint8Array(digest).slice(0, 8), (b) => b.toString(16).padStart(2, "0")).join("");
}

async function resolveDocumentScope(): Promise<string> {
  const url = Office.context.document.url || "";
  if (url) return hashKey(url);
  const settings = Office.context.document.settings;
  const saved = settings.get(DOCUMENT_SCOPE_SETTING);
  if (typeof saved === "string" && /^[A-Za-z0-9_-]{1,128}$/.test(saved)) return saved;
  const value = typeof crypto.randomUUID === "function"
    ? crypto.randomUUID()
    : `untitled-${Date.now().toString(36)}-${Math.random().toString(36).slice(2)}`;
  settings.set(DOCUMENT_SCOPE_SETTING, value);
  await new Promise<void>((resolve, reject) => settings.saveAsync((result) => {
    if (result.status === Office.AsyncResultStatus.Succeeded) resolve();
    else reject(new Error("Не удалось создать отдельный комплект для несохранённого документа"));
  }));
  return value;
}

function storage(): Storage | null {
  try {
    return window.localStorage;
  } catch {
    return null;
  }
}

function contextField(key: ContextFieldKey): HTMLInputElement | HTMLSelectElement {
  return $(`ctx-${key}`) as HTMLInputElement | HTMLSelectElement;
}

function readContext(): ActionContext {
  const ctx: ActionContext = {
    contractType: "",
    ourRole: "",
    counterpartyIdentifier: "",
    counterpartyCheck: state.counterpartyCheck ? JSON.stringify(state.counterpartyCheck) : "",
    lawyerInstruction: "",
  };
  for (const key of CONTEXT_KEYS) ctx[key] = contextField(key).value.trim();
  return ctx;
}

function saveContext(): void {
  storage()?.setItem(`wac:ctx:${state.docKey}`, JSON.stringify(readContext()));
  renderContextSummary();
}

function restoreContext(): void {
  let saved: Partial<ActionContext> = {};
  try {
    saved = JSON.parse(storage()?.getItem(`wac:ctx:${state.docKey}`) ?? "{}");
  } catch {
    saved = {};
  }
  for (const key of CONTEXT_KEYS) {
    const value = saved[key];
    if (value) contextField(key).value = value;
  }
  const details = $("context") as HTMLDetailsElement;
  details.open = storage()?.getItem("wac:ctx-open") !== "0";
  details.addEventListener("toggle", () => storage()?.setItem("wac:ctx-open", details.open ? "1" : "0"));
  renderContextSummary();
}

function renderContextSummary(): void {
  const c = readContext();
  const values = [c.contractType, c.ourRole].filter(Boolean);
  $("context-summary").textContent = values.length ? values.join(" · ") : "— не заполнено";
  const missing = [!c.contractType ? "тип договора" : "", !c.ourRole ? "наша сторона" : ""].filter(Boolean);
  const warning = $("context-warning");
  warning.textContent = missing.length
    ? `Не заполнено: ${missing.join(", ")}. Анализ продолжится, но оценка может быть менее точной.`
    : "";
  warning.hidden = missing.length === 0;
}

function normalizeIdentifier(value: string): string {
  return value.replace(/\D/g, "");
}

function validIdentifier(value: string): boolean {
  const digits = normalizeIdentifier(value);
  if (!/^(?:\d{10}|\d{12}|\d{13}|\d{15})$/.test(digits) || /^(\d)\1+$/.test(digits)) return false;
  const values = Array.from(digits, Number);
  const checksum = (weights: number[]): number => weights.reduce((sum, weight, index) => sum + weight * values[index], 0) % 11 % 10;
  if (values.length === 10) return checksum([2, 4, 10, 3, 5, 9, 4, 6, 8]) === values[9];
  if (values.length === 12) {
    return checksum([7, 2, 4, 10, 3, 5, 9, 4, 6, 8]) === values[10]
      && checksum([3, 7, 2, 4, 10, 3, 5, 9, 4, 6, 8]) === values[11];
  }
  const divisor = values.length === 13 ? 11 : 13;
  return Number(digits.slice(0, -1)) % divisor % 10 === values[values.length - 1];
}

function counterpartyAction(): ActionDescriptor | undefined {
  return state.actions.find((action) => action.id === "counterparty-check");
}

function storeCounterpartyCheck(identifier: string, result: CounterpartyCheckResponse | null): void {
  state.counterpartyCheck = result;
  storage()?.setItem(`wac:counterparty-id:${state.docKey}`, identifier);
  if (result) storage()?.setItem(`wac:counterparty-result:${state.docKey}`, JSON.stringify(result));
  else storage()?.removeItem(`wac:counterparty-result:${state.docKey}`);
  renderCounterpartyControl();
}

function restoreCounterpartyCheck(): void {
  const identifier = storage()?.getItem(`wac:counterparty-id:${state.docKey}`) ?? "";
  ($("counterparty-id") as HTMLInputElement).value = identifier;
  try {
    const saved = storage()?.getItem(`wac:counterparty-result:${state.docKey}`);
    state.counterpartyCheck = saved ? JSON.parse(saved) as CounterpartyCheckResponse : null;
    if (state.counterpartyCheck?.identifier !== identifier) {
      state.counterpartyCheck = null;
      storage()?.removeItem(`wac:counterparty-result:${state.docKey}`);
    }
  } catch {
    state.counterpartyCheck = null;
  }
  renderCounterpartyControl();
}

function renderCounterpartyControl(): void {
  const action = counterpartyAction();
  const input = $("counterparty-id") as HTMLInputElement;
  const check = $("counterparty-check") as HTMLButtonElement;
  const identifier = normalizeIdentifier(input.value);
  check.disabled = state.busy || !action?.available || !state.dadataConfigured;
  check.title = !action ? "Действие проверки не загружено" : !action.available ? action.problem : !state.dadataConfigured ? "Задайте ключи DaData в настройках приложения" : identifier && !validIdentifier(identifier) ? "Идентификатор не прошёл проверку контрольной суммы" : "";
  const status = $("counterparty-status");
  if (!action?.available) status.textContent = action?.problem || "Проверка контрагента недоступна";
  else if (!state.dadataConfigured) status.textContent = "Ключи DaData не заданы. Откройте настройки приложения.";
  else if (state.counterpartyCheck?.identifier === identifier) {
    const company = state.counterpartyCheck.company.shortName || state.counterpartyCheck.company.fullName || identifier;
    status.textContent = state.counterpartyCheck.lookupStatus === "найдено"
      ? `Проверено через DaData: ${company}`
      : `DaData: ${state.counterpartyCheck.lookupStatus}`;
  } else status.textContent = "Контрагент не проверен.";
}

async function onCounterpartyCheck(): Promise<void> {
  const action = counterpartyAction();
  if (!action) return;
  const input = $("counterparty-id") as HTMLInputElement;
  let identifier = normalizeIdentifier(input.value);
  if (identifier && !validIdentifier(identifier)) {
    showError({ code: "invalid_counterparty_id", message: "ИНН или ОГРН не прошёл проверку контрольной суммы" });
    return;
  }
  if (!identifier) {
    let preview: PrivacyPreview;
    try {
      const text = await textFor("document");
      preview = await privacyPreview(text, true);
    } catch (err) {
      const error = err as ApiError;
      showError({ code: error.code ?? "privacy_preview_failed", message: error.message });
      return;
    }
    if (!preview.identifiers.length) {
      showError({ code: "counterparty_id_required", message: "В разделе реквизитов не найден корректный ИНН или ОГРН" });
      return;
    }
    if (preview.identifiers.length === 1) identifier = preview.identifiers[0];
    else {
      const variants = preview.identifiers.map((value, index) => `${index + 1}: ${value}`).join("\n");
      const chosen = window.prompt(`В реквизитах найдено несколько идентификаторов. Введите номер контрагента:\n${variants}`, "1");
      const index = Number(chosen) - 1;
      if (!Number.isInteger(index) || index < 0 || index >= preview.identifiers.length) return;
      identifier = preview.identifiers[index];
    }
  }
  input.value = identifier;
  state.pendingCounterpartyIdentifier = identifier;
  try {
    await onAction(
      action,
      { counterpartyIdentifier: identifier, counterpartyCheck: "" },
    );
  } finally {
    state.pendingCounterpartyIdentifier = "";
  }
}

// ---------- действия ----------

const TEMPLATE_ACTION = "template-compare";
const TEMPLATE_TYPES = [
  /дополнительн[а-я]* соглаш|допсоглаш/u,
  /поставк/u,
  /услуг|оказани/u,
  /подряд|выполнени[ея] работ/u,
  /лицензи/u,
  /аренд/u,
  /перевозк|транспортн[а-я]* экспедиц/u,
  /хранени/u,
  /конфиденц|\bnda\b|неразглаш/u,
  /купл[ияе] +продаж/u,
  /пожертвован|благотвор/u,
  /дистрибьюц|дистрибуц/u,
  /агентск|комисси/u,
  /займ|заем/u,
];

function templateKeywords(value: string): string {
  return value.toLocaleLowerCase("ru-RU").replace(/ё/g, "е").replace(/[^\p{L}\p{N}]+/gu, " ").trim();
}

function templateTypes(value: string): number[] {
  const text = templateKeywords(value);
  // Допсоглашение к договору поставки остаётся допсоглашением.
  if (TEMPLATE_TYPES[0].test(text)) return [0];
  return TEMPLATE_TYPES.flatMap((pattern, index) => pattern.test(text) ? [index] : []);
}

function templateLabel(form: TypicalForm): string {
  return [form.folder, form.name].filter(Boolean).join(" / ");
}

function suggestTemplate(forms: TypicalForm[], title: string, heading: string, contractType: string): {
  ordered: TypicalForm[]; suggested: TypicalForm | undefined;
} {
  const stem = (name: string) => templateKeywords(name.replace(/\.[^.]+$/, ""));
  const titleKey = stem(title);
  const detected = templateTypes(`${titleKey} ${heading}`);
  const hint = templateTypes(contractType);
  const types = detected.includes(0) ? [0] : [...new Set([...hint, ...detected])];
  const ranked = forms.map((form) => {
    const formTypes = templateTypes(stem(form.name));
    const actualTypes = formTypes.length ? formTypes : templateTypes(form.folder);
    const match = types.some((type) => actualTypes.includes(type));
    const exact = titleKey === stem(form.name) && titleKey.length >= 12;
    return { form, score: exact ? 2 : match ? 1 : 0 };
  }).sort((left, right) => right.score - left.score || templateLabel(left.form).localeCompare(templateLabel(right.form), "ru"));
  const top = ranked[0];
  const certain = top && (top.score === 2 || (top.score === 1 && types.length === 1)) && top.score !== ranked[1]?.score;
  return { ordered: ranked.map(({ form }) => form), suggested: certain ? top.form : undefined };
}

function closeTemplatePanel(): void {
  state.templateRequest += 1;
  $("template-panel").hidden = true;
}

function updateTemplateSelection(): void {
  const select = $("template-form") as HTMLSelectElement;
  const label = select.value ? select.selectedOptions[0]?.textContent : "";
  $("template-selected").textContent = label ? `Выбрана форма: ${label}` : "";
  ($("template-run") as HTMLButtonElement).disabled = state.busy || !select.value;
}

async function openTemplatePanel(): Promise<void> {
  if (state.busy) return;
  closeInstruction();
  const request = ++state.templateRequest;
  const status = $("template-status");
  const select = $("template-form") as HTMLSelectElement;
  select.replaceChildren();
  $("template-choices").hidden = true;
  $("template-panel").hidden = false;
  updateTemplateSelection();
  status.textContent = "Читаю каталог типовых форм в SharePoint…";
  try {
    const [{ forms }, name, text] = await Promise.all([
      api<{ forms: TypicalForm[] }>("/templates"), documentName(), textFor("document"),
    ]);
    if (request !== state.templateRequest) return;
    if (!forms.length) {
      status.textContent = "В настроенной папке не найдено поддерживаемых типовых форм";
      return;
    }
    let title = name;
    try { title = decodeURIComponent(name); } catch { /* локальное имя уже пригодно для поиска */ }
    const heading = text.split(/[\r\n]+/).map((line) => line.trim()).filter(Boolean).slice(0, 4).join(" ").slice(0, 360);
    const { ordered, suggested } = suggestTemplate(forms, title, heading, readContext().contractType);
    const placeholder = el("option", "Выберите типовую форму…");
    placeholder.value = "";
    select.appendChild(placeholder);
    for (const form of ordered) {
      const option = el("option", templateLabel(form));
      option.value = form.id;
      select.appendChild(option);
    }
    select.value = suggested?.id ?? "";
    status.textContent = suggested
      ? "Предложена форма по названию или типу договора. Подтвердите её или выберите другую."
      : "Не уверен, какая форма подходит. Выберите вариант из списка. Возможные совпадения показаны первыми.";
    $("template-choices").hidden = false;
    updateTemplateSelection();
    select.focus();
  } catch (err) {
    if (request === state.templateRequest) status.textContent = (err as Error).message;
  }
}

function renderActions(): void {
  const box = $("actions");
  box.replaceChildren();
  for (const action of state.actions.filter((item) => item.id !== "counterparty-check")) {
    const needsSelection = action.input === "selection" && state.inWord && !state.hasSelection;
    const needsWritableDocument = action.autoApply && state.inWord && !state.documentWritable;
    const title = !action.available
      ? action.problem
      : needsSelection
        ? MSG.noSelection
        : needsWritableDocument
          ? documentReadOnlyMessage()
          : action.description;
    box.appendChild(
      button(action.label, () => {
        if (action.id === TEMPLATE_ACTION) void openTemplatePanel();
        else if (action.requiresInstruction) openInstruction(action);
        else void onAction(action);
      }, {
        disabled: state.busy || !action.available || needsSelection || needsWritableDocument,
        title,
      }),
    );
  }
  const unavailable = state.actions.find((a) => !a.available);
  $("selection-hint").textContent = unavailable
    ? unavailable.problem
    : state.inWord && !state.hasSelection && state.actions.some((a) => a.input === "selection")
      ? `${MSG.noSelection}, чтобы проанализировать его`
      : "";
}

function openInstruction(action: ActionDescriptor): void {
  if (state.busy || !action.available) return;
  closeTemplatePanel();
  state.instructionActionId = action.id;
  const panel = $("instruction-panel");
  const input = $("lawyer-instruction") as HTMLTextAreaElement;
  $("instruction-title").textContent = action.label;
  $("instruction-hint").textContent = "Опишите конкретную задачу. Модель проанализирует весь открытый документ только в этих границах.";
  panel.hidden = false;
  input.focus();
}

function closeInstruction(clear = false): void {
  $("instruction-panel").hidden = true;
  state.instructionActionId = "";
  if (clear) ($("lawyer-instruction") as HTMLTextAreaElement).value = "";
}

async function runInstructionAction(): Promise<void> {
  if (state.busy) return;
  const action = state.actions.find((item) => item.id === state.instructionActionId);
  const input = $("lawyer-instruction") as HTMLTextAreaElement;
  const instruction = input.value.trim();
  if (!action?.requiresInstruction) return;
  if (!instruction) {
    $("instruction-hint").textContent = "Сначала опишите, что именно нужно проверить или изменить.";
    input.focus();
    return;
  }
  closeInstruction();
  await onAction(action, { lawyerInstruction: instruction });
}

function askConfirm(text: string, yesLabel = "Отправить"): Promise<boolean> {
  $("result-zone").hidden = false;
  const box = $("confirm");
  if (!box.hidden) return Promise.resolve(false);
  $("confirm-text").textContent = text;
  ($("confirm-yes") as HTMLButtonElement).textContent = yesLabel;
  box.hidden = false;
  return new Promise((resolve) => {
    const done = (answer: boolean) => {
      box.hidden = true;
      $("confirm-yes").removeEventListener("click", yes);
      $("confirm-no").removeEventListener("click", no);
      resolve(answer);
    };
    const yes = () => done(true);
    const no = () => done(false);
    $("confirm-yes").addEventListener("click", yes);
    $("confirm-no").addEventListener("click", no);
  });
}

function setBusy(busy: boolean, progress = ""): void {
  state.busy = busy;
  $("progress").hidden = !busy;
  $("progress-text").textContent = progress;
  $("cancel-btn").hidden = !(busy && state.taskId);
  renderActions();
  renderCounterpartyControl();
  ($("instruction-run") as HTMLButtonElement).disabled = busy;
  updateTemplateSelection();
}

function renderUsage(status: TaskStatus): void {
  const box = $("usage");
  const usage = status.usage;
  const parts = [`${status.durationSeconds.toLocaleString("ru-RU", { maximumFractionDigits: 1 })} с`];
  if (usage.totalTokens) {
    const prefix = usage.estimated ? "≈ " : "";
    parts.push(`${prefix}${usage.totalTokens.toLocaleString("ru-RU")} токенов`);
    const details = [
      `вход ${usage.inputTokens.toLocaleString("ru-RU")}`,
      `выход ${usage.outputTokens.toLocaleString("ru-RU")}`,
    ];
    if (usage.cachedInputTokens) details.push(`кэш ${usage.cachedInputTokens.toLocaleString("ru-RU")}`);
    if (usage.reasoningTokens) details.push(`рассуждение ${usage.reasoningTokens.toLocaleString("ru-RU")}`);
    parts.push(details.join(", "));
  }
  if (typeof usage.costUsd === "number") parts.push(`$${usage.costUsd.toFixed(6)}`);
  box.textContent = `${usage.estimated ? "Оценка расхода" : "Расход"}: ${parts.join(" · ")}`;
  box.title = usage.estimated
    ? "Запрос не завершён: показана приблизительная оценка по уже подготовленному контексту"
    : "Фактические данные, сообщённые движком";
  box.hidden = false;
}

function clearUsage(): void {
  const box = $("usage");
  box.hidden = true;
  box.textContent = "";
  box.title = "";
}

async function onAction(
  action: ActionDescriptor,
  contextOverrides: Partial<ActionContext> = {},
  confirmationText = "",
  templateId?: string,
): Promise<void> {
  if (state.busy) return;
  closeTemplatePanel();
  const result = $("result");
  let currentUsageRendered = false;
  try {
    const text = await textFor(action.input);
    if (!text.trim()) {
      clearUsage();
      showError({ code: "empty_text", message: action.input === "document" ? MSG.emptyDocument : MSG.noSelection });
      return;
    }
    if (action.requiresConfirmation) {
      const pages = Math.max(1, Math.round(text.length / 1800));
      let privacyText = "";
      if (action.id !== "counterparty-check") {
        const preview = await privacyPreview(text, action.input === "document");
        if (!preview.safeToSend) {
          clearUsage();
          showError({ code: "privacy_boundary_uncertain", message: preview.warning || "Не удалось безопасно отделить реквизиты документа" });
          return;
        }
        const removed = [preview.headerRemoved ? "шапка" : "", preview.requisitesRemoved ? "реквизиты и подписи" : ""].filter(Boolean).join(", ");
        privacyText = `\nЛокальная защита: ${preview.redactions} замен; ${removed ? `исключены ${removed}; ` : ""}модель получит ${preview.outputChars.toLocaleString("ru-RU")} знаков.`;
        if (preview.warning) privacyText += `\nВнимание: ${preview.warning}.`;
      }
      const actionEffect = action.autoApply
        ? "\nПосле анализа найденные правки будут сразу внесены в режиме рецензирования, а к ним добавятся примечания Word."
        : "";
      const ok = await askConfirm(
        (confirmationText || `«${action.label}»: исходный документ содержит ${text.length.toLocaleString("ru-RU")} знаков (≈ ${pages} стр.).`) + privacyText + actionEffect + "\nПродолжить?",
        action.id === "counterparty-check" ? "Проверить" : action.autoApply ? "Проверить и внести" : "Отправить",
      );
      if (!ok) return;
    }
    state.analyzedText = text;
    $("result-zone").hidden = false;
    result.replaceChildren();
    clearUsage();
    setBusy(true, "Отправляю запрос");

    const payload: ActionPayload = {
      text,
      context: { ...readContext(), ...contextOverrides },
      documentName: await documentName(),
      templateId,
    };
    const handle = await runAction(action.id, payload);
    let status = handle.status;
    state.taskId = status.taskId;
    renderUsage(status);
    currentUsageRendered = true;
    if (handle.background) {
      setBusy(true, status.message ?? status.progress);
      if (status.message) toast(status.message);
      while (!FINAL.includes(status.state)) {
        await new Promise((r) => setTimeout(r, POLL_MS));
        status = await pollTask(status.taskId);
        $("progress-text").textContent = status.progress;
        renderUsage(status);
      }
    }
    await renderStatus(status, action);
  } catch (err) {
    const e = err as ApiError;
    if (!currentUsageRendered) clearUsage();
    showError({ code: e.code ?? "error", message: e.message, raw: e.raw });
  } finally {
    state.taskId = "";
    setBusy(false);
  }
}

async function cancelCurrent(): Promise<void> {
  if (!state.taskId) return;
  $("progress-text").textContent = "Отменяю…";
  try {
    await api(`/task/${encodeURIComponent(state.taskId)}/cancel`, { method: "POST" });
  } catch (err) {
    toast((err as Error).message);
  }
}

// ---------- результат ----------

async function renderStatus(status: TaskStatus, action: ActionDescriptor): Promise<void> {
  renderUsage(status);
  if (status.state === "done" && status.result) {
    const r = status.result;
    if (r.type === "card" && status.actionId === "counterparty-check") {
      const checked = r.card as CounterpartyCheckResponse;
      const normalized = { ...checked, identifier: state.pendingCounterpartyIdentifier || checked.identifier };
      storeCounterpartyCheck(normalized.identifier, normalized);
      renderCounterpartyCard(normalized);
    } else if (r.type === "card") renderCard(r.card as ClauseReviewResponse);
    else if (r.type === "review") {
      renderReview(r.review);
      if (r.template) $("result").prepend(el("p", `Сверено с формой: ${templateLabel(r.template)}`, "hint"));
      if (action.autoApply) {
        try {
          const summary = await applyReviewAutomatically(r.review);
          renderReview(r.review);
          renderAutoApplyNotice(summary);
        } catch (err) {
          renderReview(r.review);
          renderAutoApplyError((err as Error).message);
        }
      }
    }
    else if (r.type === "checklist") renderChecklist(r.checklist);
    else renderText(r.text);
  } else if (status.state === "cancelled") {
    $("result").replaceChildren(el("p", "Задача отменена", "muted"));
  } else {
    showError(status.error ?? { code: "failed", message: "Действие завершилось ошибкой" });
  }
}

function renderAutoApplyNotice(summary: AutoApplySummary): void {
  const names = summary.authors.length ? summary.authors.join(", ") : "Word не вернул имя";
  const allVerified = summary.verifiedChanges >= summary.applied
    && summary.verifiedComments >= summary.commented
    && summary.authors.length === 1;
  const verification = summary.applied + summary.commented === 0
    ? "изменения не вносились"
    : allVerified
      ? "единый автор всех исправлений и комментариев подтверждён Word"
      : "Word не подтвердил единого автора для всех изменений; проверьте область рецензирования";
  const text = [
    `Внесено правок: ${summary.applied}; добавлено комментариев: ${summary.commented}; без точной привязки: ${summary.skipped}.`,
    summary.manualEdits ? `Требуют ручного выбора текста: ${summary.manualEdits}.` : "",
    `Авторы, зарегистрированные Word: ${names}; ${verification}.`,
  ].filter(Boolean).join(" ");
  const notice = el("div", text, "auto-apply-notice");
  $("result").prepend(notice);
}

function renderAutoApplyError(message: string): void {
  const notice = el("div", `Анализ завершён, но автоматическое внесение остановлено: ${message}`, "error");
  $("result").prepend(notice);
}

const CHECKLIST_STATUS: Record<ChecklistStatus, { label: string; css: string }> = {
  ok: { label: "В порядке", css: "checklist-ok" },
  issue: { label: "Замечание", css: "checklist-issue" },
  not_applicable: { label: "Не применимо", css: "checklist-na" },
  template_skip: { label: "Не проверяется в шаблоне", css: "checklist-skip" },
  needs_review: { label: "Нужна ручная проверка", css: "checklist-review" },
};

function renderChecklist(result: ContractChecklistResponse): void {
  const box = $("result");
  box.replaceChildren();

  const counts = new Map<ChecklistStatus, number>();
  for (const finding of result.findings) counts.set(finding.status, (counts.get(finding.status) ?? 0) + 1);

  const summary = el("section", "", "checklist-summary");
  summary.appendChild(el("strong", result.isTemplate ? "Шаблон договора" : "Заполненный договор"));
  if (result.contractTypes.length) summary.appendChild(el("div", `Тип: ${result.contractTypes.join(", ")}`, "hint"));
  if (result.briefDescription) summary.appendChild(el("p", result.briefDescription));
  const counters = el("div", "", "checklist-counters");
  for (const status of ["issue", "needs_review", "ok", "not_applicable", "template_skip"] as ChecklistStatus[]) {
    const count = counts.get(status) ?? 0;
    if (!count) continue;
    counters.appendChild(el("span", `${CHECKLIST_STATUS[status].label}: ${count}`, `checklist-badge ${CHECKLIST_STATUS[status].css}`));
  }
  summary.appendChild(counters);
  box.appendChild(summary);

  const important = result.findings.filter((item) => item.status === "issue" || item.status === "needs_review");
  const passed = result.findings.filter((item) => item.status !== "issue" && item.status !== "needs_review");
  for (const [title, findings, open] of [
    ["Требуют внимания", important, true],
    ["Остальные проверки", passed, false],
  ] as Array<[string, ChecklistFinding[], boolean]>) {
    if (!findings.length) continue;
    const group = el("details", "", "checklist-group");
    group.open = open;
    group.appendChild(el("summary", `${title}: ${findings.length}`));
    for (const finding of findings) {
      const meta = CHECKLIST_STATUS[finding.status];
      const card = el("article", "", "checklist-item");
      const head = el("div", "", "checklist-head");
      head.append(
        el("strong", `${finding.checkId} · ${finding.checkName}`),
        el("span", meta.label, `checklist-badge ${meta.css}`),
      );
      card.appendChild(head);
      if (finding.description) card.appendChild(el("p", finding.description));
      if (finding.issue) card.appendChild(field("Что обнаружено", finding.issue));
      if (finding.recommendation) card.appendChild(field("Что сделать", finding.recommendation));
      group.appendChild(card);
    }
    box.appendChild(group);
  }
}

function renderCounterpartyCard(result: CounterpartyCheckResponse): void {
  const box = $("result");
  box.replaceChildren();
  const riskClass = result.lookupStatus === "найдено" ? "risk-низкий" : result.lookupStatus === "не найдено" ? "risk-средний" : "risk-высокий";
  const head = el("div", "", "card-head");
  head.appendChild(el("span", `DaData: ${result.lookupStatus}`, `risk ${riskClass}`));
  head.appendChild(el("strong", result.company.shortName || result.company.fullName || result.identifier));
  box.appendChild(head);

  const details = el("dl", "", "counterparty-details");
  const values: Array<[string, string]> = [
    ["Полное наименование", result.company.fullName],
    ["ИНН", result.company.inn],
    ["КПП", result.company.kpp],
    ["ОГРН", result.company.ogrn],
    ["Статус", result.company.status],
    ["Юридический адрес", result.company.legalAddress],
    ["Руководитель", result.company.management],
  ];
  for (const [label, value] of values) {
    if (!value) continue;
    details.append(el("dt", label), el("dd", value));
  }
  if (details.children.length) box.appendChild(details);

  if (result.summary) box.appendChild(field("Вывод", result.summary));
  if (result.lawyerNote) box.appendChild(field("Заметка юристу", result.lawyerNote));
}

function field(title: string, text: string, pre = false): HTMLElement {
  const box = el("div", "", "field");
  box.appendChild(el("h3", title));
  box.appendChild(el(pre ? "pre" : "p", text));
  return box;
}

function renderCard(result: ClauseReviewResponse): void {
  const box = $("result");
  box.replaceChildren();
  const head = el("div", "", "card-head");
  head.appendChild(el("span", result.riskLevel, `risk risk-${result.riskLevel}`));
  head.appendChild(el("span", result.clauseType, "clause-type"));
  box.appendChild(head);

  box.appendChild(field("Проблема", result.issue));
  box.appendChild(field("Рекомендация", result.recommendation));
  if (result.proposedWording) box.appendChild(field("Предлагаемая редакция", result.proposedWording, true));
  if (result.counterpartyComment) box.appendChild(field("Комментарий контрагенту", result.counterpartyComment, true));
  if (result.lawyerNote) box.appendChild(field("Заметка юристу", result.lawyerNote));
  if (result.needMoreContext) {
    const warn = el("div", "", "warn");
    warn.appendChild(el("strong", "Оценка предварительная — не хватает контекста"));
    if (result.contextNeeded.length) {
      const list = el("ul");
      for (const item of result.contextNeeded) list.appendChild(el("li", item));
      warn.appendChild(list);
    }
    box.appendChild(warn);
  }

  const commentText = result.counterpartyComment || `${result.issue}\n${result.recommendation}`;
  const row = el("div", "", "row");
  row.append(
    button("Скопировать рекомендацию", () => void copyToClipboard(result.recommendation)),
    button("Скопировать редакцию", () => void copyToClipboard(result.proposedWording), { disabled: !result.proposedWording }),
    button("Скопировать комментарий", () => void copyToClipboard(result.counterpartyComment), { disabled: !result.counterpartyComment }),
    button("Вставить редакцию в документ", () => void guarded(() => insertProposedWording(result.proposedWording)), {
      primary: true,
      disabled: !result.proposedWording || !state.inWord || !state.documentWritable,
      title: !state.inWord ? MSG.notInWord : !state.documentWritable ? documentReadOnlyMessage() : "",
    }),
    button("Добавить примечание Word", () => void guarded(() => addWordComment(commentText)), {
      disabled: !state.inWord || !state.documentWritable,
      title: !state.inWord ? MSG.notInWord : !state.documentWritable ? documentReadOnlyMessage() : "",
    }),
  );
  box.appendChild(row);
}

function renderReview(result: ContractReviewResponse): void {
  const box = $("result");
  box.replaceChildren();

  const summary = el("section", "", "review-summary");
  summary.appendChild(el("div", result.overallRecommendation, "overall-recommendation"));
  summary.appendChild(field("Вывод", result.summary));
  if (result.businessQuestions.length) {
    const questions = el("div", "", "field");
    questions.appendChild(el("h3", "Вопросы бизнесу"));
    const list = el("ul");
    for (const question of result.businessQuestions) list.appendChild(el("li", question));
    questions.appendChild(list);
    summary.appendChild(questions);
  }
  if (result.lawyerNote) summary.appendChild(field("Заметка юристу", result.lawyerNote));
  box.appendChild(summary);
  box.appendChild(el("h2", `Замечания: ${result.findings.length}`, "review-title"));

  if (!result.findings.length) {
    box.appendChild(el("p", "Замечаний не найдено", "muted"));
    return;
  }

  for (const finding of result.findings) {
    const card = el("article", "", "finding");
    const head = el("div", "", "card-head");
    head.appendChild(el("span", finding.riskLevel, `risk risk-${finding.riskLevel}`));
    head.appendChild(el("strong", [finding.id, finding.clauseReference].filter(Boolean).join(" · ")));
    head.appendChild(el("span", finding.clauseType, "clause-type"));
    card.appendChild(head);
    const bindingIssue = sourceBindingIssue(finding.sourceText);
    const editIssue = findingEditIssue(finding);
    const hasWording = !!finding.proposedWording.trim();
    if (finding.sourceText) card.appendChild(field("Исходный фрагмент", finding.sourceText, true));
    card.appendChild(field("Проблема", finding.issue));
    card.appendChild(field("Рекомендация", finding.recommendation));
    if (finding.proposedWording) card.appendChild(field("Предлагаемая редакция", finding.proposedWording, true));
    if (finding.counterpartyComment) card.appendChild(field("Комментарий контрагенту", finding.counterpartyComment, true));
    const visibleLawyerNote = finding.lawyerNote
      .split("\n")
      .filter((line) => !line.startsWith(BINDING_WARNING_PREFIX))
      .join("\n")
      .trim();
    if (visibleLawyerNote) card.appendChild(field("Заметка юристу", visibleLawyerNote));

    const linked = !bindingIssue && state.inWord;
    const editable = linked && state.documentWritable;
    const canUseSelection = state.inWord && state.documentWritable && hasWording;
    if (bindingIssue || editIssue) {
      const hint = bindingIssue && canUseSelection
        ? `${bindingIssue}. Чтобы заменить существующий текст, выделите его в Word и нажмите «Заменить выделение…»`
        : bindingIssue || editIssue;
      card.appendChild(el("div", hint, "warn"));
    }
    const row = el("div", "", "row");
    row.append(
      button("Перейти к пункту", () => void guarded(() => navigateToFinding(finding)), {
        disabled: !linked,
        title: state.inWord ? bindingIssue : MSG.notInWord,
      }),
      button("Внести правку", () => void guarded(() => applyFinding(finding)), {
        primary: true,
        disabled: !editable || !hasWording || !!editIssue,
        title: !state.inWord ? MSG.notInWord : bindingIssue || (!state.documentWritable ? documentReadOnlyMessage() : editIssue || (!hasWording ? "Нет предлагаемой редакции" : "")),
      }),
      button("Добавить примечание", () => void guarded(() => commentFinding(finding)), {
        disabled: !editable,
        title: !state.inWord ? MSG.notInWord : bindingIssue || (!state.documentWritable ? documentReadOnlyMessage() : ""),
      }),
    );
    if (canUseSelection) row.appendChild(button("Заменить выделение…", () => void guarded(() => applyFindingToSelection(finding))));
    row.appendChild(button("Скопировать", () => void copyToClipboard(finding.proposedWording || finding.recommendation)));
    card.appendChild(row);
    box.appendChild(card);
  }
}

function renderText(text: string): void {
  const box = $("result");
  box.replaceChildren(field("Ответ", text, true));
  const row = el("div", "", "row");
  row.appendChild(button("Скопировать", () => void copyToClipboard(text)));
  box.appendChild(row);
}

function showError(error: TaskError): void {
  $("result-zone").hidden = false;
  const box = el("div", "", "error");
  box.appendChild(el("strong", error.message));
  if (error.raw) {
    box.appendChild(el("pre", error.raw));
    const row = el("div", "", "row");
    row.appendChild(button("Скопировать ответ", () => void copyToClipboard(error.raw ?? "")));
    box.appendChild(row);
  }
  $("result").replaceChildren(box);
}

async function guarded(fn: () => Promise<unknown>): Promise<void> {
  try {
    await fn();
  } catch (err) {
    toast((err as Error).message);
  }
}

// ---------- состояние хоста и выделение ----------

async function refreshHealth(): Promise<boolean> {
  const status = $("status");
  try {
    const h = await api<Health>("/health");
    state.dadataConfigured = h.dadataConfigured;
    const ok = h.engineAvailable && h.projectValid;
    status.className = `status ${ok ? "ok" : "bad"}`;
    status.textContent = h.engineAvailable ? ENGINE_NAMES[h.engine] ?? h.engine : "движок не найден";
    status.title = h.engineVersion;
    banner(!h.projectValid ? h.projectMessage : !h.engineAvailable ? h.engineMessage : h.actionsError);
    renderCounterpartyControl();
    return true;
  } catch (err) {
    state.dadataConfigured = false;
    status.className = "status bad";
    status.textContent = "не подключено";
    banner((err as Error).message);
    renderCounterpartyControl();
    return false;
  }
}

let selectionTimer = 0;
function onSelectionChanged(): void {
  window.clearTimeout(selectionTimer);
  selectionTimer = window.setTimeout(async () => {
    try {
      state.hasSelection = normalize(await getSelectedText()).length > 0;
    } catch {
      state.hasSelection = false;
    }
    if (!state.busy) renderActions();
  }, 250);
}

async function start(inWord: boolean): Promise<void> {
  $("pairing-form").addEventListener("submit", (event) => {
    event.preventDefault();
    void pairPanel();
  });
  state.inWord = inWord;
  state.documentWritable = inWord && Office.context.document.mode === Office.DocumentMode.ReadWrite;
  $("debug-input").hidden = inWord;
  if (inWord) {
    state.docKey = await resolveDocumentScope();
    Office.context.document.addHandlerAsync(Office.EventType.DocumentSelectionChanged, onSelectionChanged);
    onSelectionChanged();
  }
  restoreContext();
  restoreCounterpartyCheck();
  for (const key of CONTEXT_KEYS) contextField(key).addEventListener("change", saveContext);
  $("counterparty-id").addEventListener("input", () => {
    const identifier = normalizeIdentifier(($('counterparty-id') as HTMLInputElement).value);
    if (state.counterpartyCheck && state.counterpartyCheck.identifier !== identifier) storeCounterpartyCheck(identifier, null);
    else {
      storage()?.setItem(`wac:counterparty-id:${state.docKey}`, identifier);
      renderCounterpartyControl();
    }
  });
  $("counterparty-check").addEventListener("click", () => void onCounterpartyCheck());
  $("instruction-run").addEventListener("click", () => void runInstructionAction());
  $("template-form").addEventListener("change", updateTemplateSelection);
  $("template-cancel").addEventListener("click", closeTemplatePanel);
  $("template-run").addEventListener("click", () => {
    const action = state.actions.find((item) => item.id === TEMPLATE_ACTION && item.available);
    const id = ($("template-form") as HTMLSelectElement).value;
    if (action && id) void onAction(action, {}, "", id);
  });
  $("instruction-cancel").addEventListener("click", () => closeInstruction());
  $("cancel-btn").addEventListener("click", () => void cancelCurrent());
  $("related-add").addEventListener("click", () => ($("related-files") as HTMLInputElement).click());
  $("related-files").addEventListener("change", () => {
    const files = ($("related-files") as HTMLInputElement).files;
    if (files) void uploadRelatedDocuments(files);
  });

  try { sessionToken = window.sessionStorage.getItem(SESSION_KEY) ?? ""; } catch { /* WebView может запрещать хранилище */ }
  if (sessionToken) await loadWorkspace();
}

async function pairPanel(): Promise<void> {
  const input = $("pairing-code") as HTMLInputElement;
  const submit = $("pairing-submit") as HTMLButtonElement;
  if (submit.disabled) return;
  submit.disabled = true;
  try {
    const result = await api<{ token: string }>("/session/pair", {
      method: "POST", body: JSON.stringify({ code: input.value.trim() }),
    });
    sessionToken = result.token;
    try { window.sessionStorage.setItem(SESSION_KEY, sessionToken); } catch { /* остаётся в памяти страницы */ }
    input.value = "";
    await loadWorkspace();
  } catch (error) {
    banner((error as Error).message);
  } finally {
    submit.disabled = false;
  }
}

async function loadWorkspace(): Promise<void> {
  if (!await refreshHealth() || !sessionToken) return;
  $("pairing").hidden = true;
  $("workspace").hidden = false;
  try {
    await loadRelatedDocuments();
  } catch (err) {
    $("related-summary").textContent = (err as Error).message;
  }
  try {
    state.actions = await loadActions();
  } catch (err) {
    banner((err as Error).message);
  }
  renderActions();
  renderCounterpartyControl();
}

if (typeof Office !== "undefined") {
  Office.onReady((info) => void start(info.host === Office.HostType.Word));
} else {
  // office.js не загрузился (страница открыта в браузере без Word) — режим отладки.
  void start(false);
}
