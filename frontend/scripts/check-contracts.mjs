import fs from "node:fs";
import path from "node:path";
import process from "node:process";
import ts from "typescript";

const root = process.cwd();
const source = fs.readFileSync(path.join(root, "lib/contracts.ts"), "utf8");
// Both fixtures are produced by the Pydantic models, so a mismatch here means the
// TypeScript mirror has drifted from the validation authority — not that the data is odd.
// The closed run carries a final report, which the open one cannot.
const FIXTURES = ["run-snapshot", "closed-run-snapshot"];
const fixtures = FIXTURES.map((name) =>
  JSON.parse(
    fs.readFileSync(
      path.join(root, `../contracts/examples/${name}.json`),
      "utf8",
    ),
  ),
);
const virtual = path.join(root, "lib/__contract-check.ts");
const sourceFile = path.join(root, "lib/contracts.ts");
const check =
  `import type { RunSnapshot } from "./contracts";\n` +
  fixtures
    .map(
      (fixture, index) =>
        `const fixture${index} = ${JSON.stringify(fixture)} as const satisfies RunSnapshot;\nvoid fixture${index};\n`,
    )
    .join("");

const options = {
  strict: true,
  noEmit: true,
  target: ts.ScriptTarget.ES2022,
  module: ts.ModuleKind.ESNext,
  moduleResolution: ts.ModuleResolutionKind.Bundler,
  skipLibCheck: true,
};
const host = ts.createCompilerHost(options);
const originalSource = host.getSourceFile.bind(host);
host.fileExists = (fileName) =>
  fileName === virtual ||
  fileName === sourceFile ||
  ts.sys.fileExists(fileName);
host.readFile = (fileName) =>
  fileName === virtual
    ? check
    : fileName === sourceFile
      ? source
      : ts.sys.readFile(fileName);
host.getSourceFile = (fileName, languageVersion) => {
  if (fileName === virtual)
    return ts.createSourceFile(fileName, check, languageVersion, true);
  if (fileName === sourceFile)
    return ts.createSourceFile(fileName, source, languageVersion, true);
  return originalSource(fileName, languageVersion);
};
const diagnostics = ts.getPreEmitDiagnostics(
  ts.createProgram([virtual, sourceFile], options, host),
);
if (diagnostics.length) {
  console.error(
    ts.formatDiagnosticsWithColorAndContext(diagnostics, {
      getCanonicalFileName: (name) => name,
      getCurrentDirectory: () => root,
      getNewLine: () => "\n",
    }),
  );
  process.exit(1);
}
console.log(
  `PASS ${FIXTURES.length} synthetic snapshots satisfy the TypeScript contract mirror`,
);
