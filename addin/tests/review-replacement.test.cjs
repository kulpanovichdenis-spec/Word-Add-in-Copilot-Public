const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const test = require("node:test");
const { setImmediate } = require("node:timers/promises");
const vm = require("node:vm");
const ts = require("typescript");

const taskpane = fs.readFileSync(path.join(__dirname, "../src/taskpane/taskpane.ts"), "utf8");
const script = new vm.Script(ts.transpileModule(taskpane + `
  globalThis.review = { applyFinding, applyFindingToSelection, applyReviewAutomatically, findingEditIssue, sourceBindingIssue };
  state.inWord = true;
  state.documentWritable = true;
`, { compilerOptions: { target: ts.ScriptTarget.ES2020 } }).outputText);

function wordMock(entries, selection) {
  const replacements = [];
  const comments = [];
  const buttons = new Map();
  const elements = new Map();
  function getElementById(id) {
    if (!elements.has(id)) elements.set(id, {
      hidden: true,
      scrollIntoView() {},
      addEventListener: (_type, click) => buttons.set(id, click),
      removeEventListener: () => buttons.delete(id),
    });
    return elements.get(id);
  }
  const ctx = {
    sync: async () => {},
    document: {
      getSelection() {
        const { text, location } = selection;
        return {
          location,
          getReviewedText: () => ({ value: text }),
          compareLocationWith: (other) => ({ value: other.location === location ? "Equal" : "Different" }),
          insertText: (newText) => {
            replacements.push({ source: text, text: newText });
            return { getTrackedChanges: () => ({ load() {}, items: [{ author: "Юрист" }] }) };
          },
        };
      },
      body: {
        search(source) {
          const entry = entries[source];
          const range = {
            getReviewedText: () => ({ value: entry.current }),
            paragraphs: { getFirst: () => ({ getReviewedText: () => ({ value: entry.paragraph }) }) },
            insertText(text) {
              replacements.push({ source, text });
              return range;
            },
            getTrackedChanges: () => ({ load() {}, items: [{ author: "Юрист" }] }),
            insertComment(text) {
              comments.push({ source, text });
              return { load() {}, authorName: "Юрист" };
            },
          };
          return { load() {}, items: entry ? [range] : [] };
        },
      },
    },
  };
  const sandbox = {
    document: { querySelector: () => null, getElementById },
    Office: { onReady() {}, context: { requirements: { isSetSupported: () => true } } },
    Word: {
      run: (fn) => fn(ctx),
      ChangeTrackingVersion: { current: "Current" },
      ChangeTrackingMode: { trackAll: "TrackAll" },
      InsertLocation: { replace: "Replace" },
      LocationRelation: { equal: "Equal" },
    },
    window: { clearTimeout() {}, setTimeout() {} },
  };
  script.runInNewContext(sandbox);
  return {
    ...sandbox.review,
    replacements,
    comments,
    preview: () => elements.get("confirm-text").textContent,
    confirm: () => buttons.get("confirm-yes")(),
  };
}

function finding(sourceText, proposedWording) {
  return { sourceText, proposedWording, counterpartyComment: "Уточняем условие" };
}

test("numbered or multiline wording cannot replace a short anchor automatically", async () => {
  const source = "Исполнитель передаёт ролик";
  const word = wordMock({ [source]: { current: source } });
  const numbered = finding(source, "1.1. Исполнитель передаёт ролик по акту.");
  assert.match(word.findingEditIssue(numbered), /нумерацию/);
  assert.match(word.findingEditIssue(finding(source, "Новая редакция\nНовый абзац")), /несколько строк/);
  assert.match(word.sourceBindingIssue("Пункт первый\nПункт второй"), /разрыв строки/);
  await assert.rejects(word.applyFinding(numbered), /нумерацию/);
  assert.equal(word.replacements.length, 0);
});

test("numbered rewrite requires the whole paragraph and current reviewed text", async () => {
  const source = "1. Поставка осуществляется";
  const replacement = finding(source, "1. Поставка осуществляется по заявкам.");
  const word = wordMock({ [source]: { current: source, paragraph: `${source} в день оплаты.` } });
  await assert.rejects(word.applyFinding(replacement), /часть нумерованного абзаца/);
  assert.equal(word.replacements.length, 0);
  const obsolete = wordMock({ [source]: { current: "", paragraph: "1. Новая редакция." } });
  await assert.rejects(obsolete.applyFinding(replacement), /Текущий текст Word отличается/);
  assert.equal(obsolete.replacements.length, 0);
});

test("automatic review still applies a precise edit and leaves the complex rewrite for manual selection", async () => {
  const long = "Порядок передачи результата";
  const short = "в течение 5 рабочих дней";
  const word = wordMock({ [long]: { current: long }, [short]: { current: short } });
  const summary = await word.applyReviewAutomatically({ findings: [
    finding(long, "1.1. Порядок передачи результата"),
    finding(short, "в течение 10 рабочих дней"),
  ] });
  assert.deepEqual(word.replacements, [{ source: short, text: "в течение 10 рабочих дней" }]);
  assert.equal(word.comments.length, 2);
  assert.equal(summary.applied, 1);
  assert.equal(summary.manualEdits, 1);
});

test("manual replacement previews the selection and refuses a moved selection with identical text", async () => {
  const selection = { text: "1. Поставка осуществляется по заявкам.", location: 1 };
  const word = wordMock({}, selection);
  const rewrite = finding("", "1.1. Поставка осуществляется по заявкам покупателя.");
  const stale = word.applyFindingToSelection(rewrite);
  await setImmediate();
  assert.ok(word.preview().includes(selection.text));
  selection.location = 2;
  word.confirm();
  await assert.rejects(stale, /Выделение изменилось/);
  assert.equal(word.replacements.length, 0);

  const current = word.applyFindingToSelection(rewrite);
  await setImmediate();
  word.confirm();
  await current;
  assert.deepEqual(word.replacements, [{ source: selection.text, text: rewrite.proposedWording }]);
});
