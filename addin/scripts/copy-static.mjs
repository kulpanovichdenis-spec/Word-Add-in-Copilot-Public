// Копирует статику панели (html, css, иконки) в dist/ рядом со скомпилированным taskpane.js.
import { cpSync, mkdirSync } from "node:fs";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";

const root = join(dirname(fileURLToPath(import.meta.url)), "..");
const src = join(root, "src", "taskpane");
const dist = join(root, "dist");

mkdirSync(dist, { recursive: true });
for (const name of ["taskpane.html", "taskpane.css"]) {
  cpSync(join(src, name), join(dist, name));
}
cpSync(join(src, "assets"), join(dist, "assets"), { recursive: true });
console.log("static copied to", dist);
