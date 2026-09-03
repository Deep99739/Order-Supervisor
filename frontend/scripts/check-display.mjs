/**
 * Focused checks for the display rules that could make the console misreport a run.
 *
 * These are deliberately narrow. Nothing here tests a copied Radix primitive, a colour,
 * or a layout. What is covered is the small amount of real logic in `lib/`: the states
 * where "requested" must not be shown as "applied", the one backend rule the console
 * mirrors, and the two places where a wrong answer would reorder or relabel history.
 *
 * Run with `npm run check:display`. The TypeScript sources are transpiled in memory, so
 * the checks read the same files the app ships.
 */
import assert from "node:assert/strict";
import fs from "node:fs";
import path from "node:path";
import process from "node:process";
import ts from "typescript";

const root = process.cwd();
const loaded = new Map();

function load(id) {
  if (loaded.has(id)) return loaded.get(id);
  const source = fs.readFileSync(path.join(root, `${id}.ts`), "utf8");
  const { outputText } = ts.transpileModule(source, {
    compilerOptions: {
      module: ts.ModuleKind.CommonJS,
      target: ts.ScriptTarget.ES2022,
      verbatimModuleSyntax: false,
    },
  });
  const shell = { exports: {} };
  loaded.set(id, shell.exports);
  const resolve = (specifier) =>
    load(path.posix.join(path.posix.dirname(id), specifier));
  new Function("require", "exports", "module", outputText)(
    resolve,
    shell.exports,
    shell,
  );
  loaded.set(id, shell.exports);
  return shell.exports;
}

const display = load("lib/display");
const policy = load("lib/policy");
const activity = load("lib/activity");

let failures = 0;
function check(name, run) {
  try {
    run();
    console.log(`PASS ${name}`);
  } catch (error) {
    failures += 1;
    console.error(`FAIL ${name}\n     ${error.message.split("\n")[0]}`);
  }
}

// --------------------------------------------------- requested is not applied

check("a requested pause is shown as pausing, never as paused", () => {
  const state = display.supervisorState({
    status: "evaluating",
    pending_control: "pause",
  });
  assert.equal(state.label, "Pausing");
  assert.match(state.hint, /not paused yet/);
});

check("paused is shown only once the run itself reports it", () => {
  assert.equal(
    display.supervisorState({ status: "paused", pending_control: "pause" }).label,
    "Paused",
  );
  assert.equal(
    display.supervisorState({ status: "paused", pending_control: null }).label,
    "Paused",
  );
});

check("an interrupt is the same hold as a pause", () => {
  assert.equal(
    display.supervisorState({ status: "sleeping", pending_control: "interrupt" })
      .label,
    "Pausing",
  );
});

check("a requested termination is not shown as closed", () => {
  const state = display.supervisorState({
    status: "sleeping",
    pending_control: "terminate",
  });
  assert.equal(state.label, "Terminating");
  assert.match(state.hint, /not been recorded yet/);
});

check("a manually terminated run is not coloured as a failure", () => {
  assert.equal(
    display.supervisorState({ status: "terminated", pending_control: null }).tone,
    "stopped",
  );
  assert.equal(
    display.supervisorState({ status: "awaiting_recovery", pending_control: null })
      .tone,
    "alert",
  );
});

// ------------------------------------- the one backend rule the console mirrors

const template = (overrides = {}) => ({
  supervisor: {
    prioritize_speed: false,
    escalate_shipment_delays: false,
    customer_review_default: false,
    ...overrides,
  },
  instructions: [],
});

check("free text leaves customer contact held for review", () => {
  const result = policy.effectivePolicy({
    ...template(),
    instructions: [{ policy_changes: null }],
  });
  assert.equal(result.requireCustomerReview, true);
  assert.equal(result.reviewFromAmbiguity, true);
});

check("an explicit stance beats an unclassified instruction", () => {
  const result = policy.effectivePolicy({
    ...template(),
    instructions: [
      { policy_changes: null },
      { policy_changes: { require_customer_review: false } },
    ],
  });
  assert.equal(result.requireCustomerReview, false);
  assert.equal(result.reviewFromAmbiguity, false);
});

check("a template that already requires review is not called ambiguous", () => {
  const result = policy.effectivePolicy({
    ...template({ customer_review_default: true }),
    instructions: [{ policy_changes: null }],
  });
  assert.equal(result.requireCustomerReview, true);
  assert.equal(result.reviewFromAmbiguity, false);
});

check("named controls override the frozen template", () => {
  const result = policy.effectivePolicy({
    ...template(),
    instructions: [
      { policy_changes: { prioritize_speed: true, escalate_shipment_delays: true } },
    ],
  });
  assert.equal(result.prioritizeSpeed, true);
  assert.equal(result.escalateShipmentDelays, true);
  // Still unclassified for customer contact, so the hold applies.
  assert.equal(result.requireCustomerReview, true);
});

// --------------------------------------------------------- history stays honest

const record = (sequence, kind, decisionId, extra = {}) => ({
  id: `r${sequence}`,
  sequence,
  kind,
  decision_id: decisionId,
  disposition: "recorded",
  explanation: "",
  details: {},
  recorded_at: "2026-09-03T00:00:00Z",
  ...extra,
});

check("an event recorded mid-review is not absorbed into that review", () => {
  const groups = activity.groupRecords([
    record(1, "decision", "d/1"),
    record(2, "event", null),
    record(3, "decision", "d/1"),
    record(4, "action", "d/1"),
  ]);
  assert.equal(groups.length, 3);
  assert.deepEqual(
    groups.map((group) => group.records.map((item) => item.sequence)),
    [[1], [2], [3, 4]],
  );
});

check("grouping never reorders records", () => {
  const input = [
    record(5, "action", "d/2"),
    record(6, "sleep", "d/2"),
    record(7, "memory", null),
  ];
  const flattened = activity
    .groupRecords(input)
    .flatMap((group) => group.records.map((item) => item.sequence));
  assert.deepEqual(flattened, [5, 6, 7]);
});

check("a blocked proposal is never titled or toned as something that happened", () => {
  const blocked = record(8, "action", "d/3", {
    disposition: "blocked",
    details: { action: "message_customer", reason: "repeated_contact" },
  });
  assert.equal(activity.recordTitle(blocked), "Message to the customer");
  assert.equal(display.DISPOSITION_LABEL[blocked.disposition], "Blocked");
  assert.notEqual(display.DISPOSITION_TONE[blocked.disposition], "done");
  assert.equal(display.DISPOSITION_TONE.committed, "done");
  assert.equal(display.DISPOSITION_TONE.proposed, "stopped");
});

check("an unfamiliar event type is labelled, not hidden", () => {
  assert.match(display.eventLabel("carrier_lost_parcel"), /Unfamiliar event/);
  assert.equal(display.eventLabel("delivered"), "Delivered");
});

// The API's own category map, from backend/app/contracts/run.py.
const CATEGORIES = {
  event: "events",
  policy: "events",
  decision: "actions",
  action: "actions",
  review: "actions",
  run_reserved: "system",
  instruction: "system",
  control: "system",
  sleep: "system",
  memory: "system",
  continuation: "system",
  recovery: "system",
  finalization: "system",
};

check("the feed's filters match the API's categories", () => {
  for (const [kind, expected] of Object.entries(CATEGORIES)) {
    assert.equal(
      activity.categoryOf(record(1, kind, null)),
      expected,
      `${kind} should filter as ${expected}`,
    );
  }
});

// ------------------------------------------------------------------- countdown

check("a passed deadline counts down to zero, never below it", () => {
  const now = Date.parse("2026-09-03T00:10:00Z");
  assert.equal(display.countdown("2026-09-03T00:09:00Z", now), "00:00");
  assert.equal(display.countdown("2026-09-03T00:10:30Z", now), "00:30");
  assert.equal(display.countdown("2026-09-03T01:40:00Z", now), "1:30:00");
  assert.equal(display.untilTime("2026-09-03T00:09:00Z", now), "due");
});

// --------------------------------------------------------------- wake guidance

check("a hint the run has stopped honouring is not shown as if it still applied", () => {
  const now = Date.parse("2026-09-03T00:10:00Z");
  const hint = {
    kind: "watch_for_progress",
    expires_at: "2026-09-03T02:00:00Z",
    issue_id: "payment",
    event_type: "payment_confirmed",
    review_after_seconds: null,
  };
  const base = {
    control_epoch: 1,
    facts: { open_issues: [{ issue_id: "payment" }] },
    wake_guidance: { version: 1, context: { control_epoch: 1 }, hints: [hint] },
  };

  assert.equal(policy.hintStatus(base, hint, now).applies, true, "an open concern keeps it");

  const settled = { ...base, facts: { open_issues: [] } };
  assert.equal(policy.hintStatus(settled, hint, now).applies, false);
  assert.match(policy.hintStatus(settled, hint, now).why, /settled/);

  const expired = { ...hint, expires_at: "2026-09-03T00:09:00Z" };
  assert.equal(policy.hintStatus(base, expired, now).applies, false);
  assert.match(policy.hintStatus(base, expired, now).why, /expired/);

  const moved = { ...base, control_epoch: 2 };
  assert.equal(policy.hintStatus(moved, hint, now).applies, false);
  assert.match(policy.hintStatus(moved, hint, now).why, /operator boundary/);
});

console.log(
  failures === 0
    ? "\nPASS display rules"
    : `\nFAIL ${failures} display check(s)`,
);
process.exit(failures === 0 ? 0 : 1);
