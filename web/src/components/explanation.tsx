/**
 * The two explanations an approval carries, made visibly different.
 *
 * This component is the project's core principle rendered: `risk_explanation`
 * is produced by the **policy engine**, deterministically, from the tool's
 * permission class and the arguments alone; `reason` is the **model's** own
 * justification, and it is untrusted text.
 *
 * That distinction is not a labelling nicety. `risk_explanation` is the output
 * of code that has read the tool registry and the arguments, and it says the
 * same thing for the same call every time. `reason` is whatever the model
 * emitted, and `docs/architecture.md` §2 and
 * `tests/security/test_prompt_injection.py` both establish that a model can be
 * induced to emit reassuring text by retrieved content. An operator who reads
 * the model's sentence and believes it is being told the truth by the system is
 * exactly the failure the five gates exist to prevent — the gate held, but the
 * human's understanding of why did not.
 *
 * So the two are not two paragraphs in a card with a heading each. The
 * difference is carried by **four independent signals**, because any one of them
 * can be missed by a reader who is skimming a refund at 3am:
 *
 * 1. **Position.** The deterministic text comes first and the model's second.
 *    Reading order is not decoration; it is the order the reasoning is safe in.
 * 2. **Attribution.** An explicit badge on each, naming *who wrote it*, not
 *    what kind of field it is.
 * 3. **Border and background.** The model's text is quoted: a left rule, a
 *    tinted ground, monospace. It reads as a quotation of something external
 *    rather than as the interface speaking.
 * 4. **Wording.** The labels say where the text came from, in the imperative
 *    form: "computed by the policy engine" and "written by the model".
 *
 * What is deliberately *not* claimed: this does not say the model's reason is
 * false. It says it is not evidence. An operator may find it persuasive and
 * wrong, helpful and coincidental, or accurate — the point is that accuracy is
 * the one property it cannot be relied on to have, because nothing verifies it
 * before it reaches this screen.
 */

export function TrustedExplanation({ text }: { text: string }) {
  return (
    <div className="rounded-md border border-emerald-300 bg-emerald-50/70 p-3 dark:border-emerald-800 dark:bg-emerald-950/30">
      <div className="mb-1.5 flex flex-wrap items-center gap-2">
        <Badge tone="trusted">Deterministic</Badge>
        <span className="text-xs font-medium text-emerald-900 dark:text-emerald-200">
          Computed by the policy engine
        </span>
      </div>
      <p className="text-sm text-emerald-950 dark:text-emerald-50">{text}</p>
      <p className="mt-2 text-xs text-emerald-800/80 dark:text-emerald-300/80">
        Derived from the tool&rsquo;s permission class and the arguments below. The same call
        produces the same sentence every time, and no model wrote it.
      </p>
    </div>
  );
}

export function UntrustedExplanation({ text }: { text: string }) {
  return (
    <div className="rounded-md border-l-4 border-neutral-400 bg-neutral-100 p-3 dark:border-neutral-600 dark:bg-neutral-800/60">
      <div className="mb-1.5 flex flex-wrap items-center gap-2">
        <Badge tone="untrusted">Untrusted</Badge>
        <span className="text-xs font-medium text-neutral-700 dark:text-neutral-200">
          Written by the model
        </span>
      </div>
      {/* Monospace and quotation marks, together, are what make this read as a
          quotation of external text rather than as the interface reporting a
          fact. The policy engine's text above is in the body font. */}
      <blockquote className="border-l-2 border-neutral-300 pl-3 font-mono text-sm text-neutral-800 dark:border-neutral-500 dark:text-neutral-100">
        &ldquo;{text}&rdquo;
      </blockquote>
      <p className="mt-2 text-xs text-neutral-600 dark:text-neutral-400">
        The model&rsquo;s own justification. It is shown because an operator may find it useful,
        not because it has been verified &mdash; nothing checks it before it reaches this screen,
        and retrieved text can induce a model to write something reassuring. Weigh the
        deterministic explanation above, the tool, and the arguments.
      </p>
    </div>
  );
}

function Badge({ tone, children }: { tone: "trusted" | "untrusted"; children: React.ReactNode }) {
  const classes =
    tone === "trusted"
      ? "bg-emerald-600 text-white"
      : "bg-neutral-600 text-white dark:bg-neutral-400 dark:text-neutral-900";
  return (
    <span
      className={`inline-flex items-center rounded px-1.5 py-0.5 text-[11px] font-bold uppercase tracking-wider ${classes}`}
    >
      {children}
    </span>
  );
}