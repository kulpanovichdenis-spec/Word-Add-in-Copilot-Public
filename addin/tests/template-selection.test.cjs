const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const test = require("node:test");
const vm = require("node:vm");
const ts = require("typescript");

const source = fs.readFileSync(path.join(__dirname, "../src/taskpane/taskpane.ts"), "utf8");
const script = ts.transpileModule(`${source}\nglobalThis.suggest = suggestTemplate;`, {
  compilerOptions: { target: ts.ScriptTarget.ES2020 },
}).outputText;
const sandbox = { document: { querySelector: () => null }, Office: { onReady() {} } };
vm.runInNewContext(script, sandbox);
const suggest = sandbox.suggest;

const supply = { id: "supply", name: "Договор поставки.docx", folder: "Товары" };
const services = { id: "service", name: "Договор оказания услуг.docx", folder: "Услуги" };
const amendment = { id: "amend", name: "Дополнительное соглашение к договору поставки.doc", folder: "Товары" };

test("suggests the sole compatible form and distinguishes an amendment from its main agreement", () => {
  assert.equal(suggest([amendment, services, supply], "", "Договор поставки №1", "").suggested, supply);
  assert.equal(suggest([amendment, services, supply], "", "Дополнительное соглашение к договору поставки", "поставка").suggested, amendment);
});

test("keeps plausible variants first and asks for selection if they are tied", () => {
  const alternate = { id: "alternate", name: "Договор поставки оборудования.docx", folder: "Товары" };
  const result = suggest([services, alternate, supply], "Договор 3.docx", "", "поставка");
  assert.equal(result.suggested, undefined);
  assert.equal(result.ordered.at(-1), services);
  assert.equal(suggest([supply], "Договор 3.docx", "", "").suggested, undefined);
  assert.equal(suggest([services, supply], "", "Договор поставки", "услуги").suggested, undefined);
});
