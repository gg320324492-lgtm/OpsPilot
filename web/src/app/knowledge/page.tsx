import { EmptyState, ErrorPanel, Page, Panel, Time } from "@/components/ui";
import { formatTimestamp } from "@/lib/format";
import { loadKnowledge } from "@/lib/data";
import { ReindexButton } from "./reindex-button";

/**
 * Knowledge: what is indexed, and the one button that rebuilds it.
 *
 * `GET /api/knowledge` is the ground truth for whether retrieval has anything to
 * cite. That is why this screen exists rather than being folded into a settings
 * page: a run that cites nothing and a run that cites the wrong chunk look the
 * same from the run's side, and the difference is almost always visible here --
 * a document with zero chunks cannot be cited (`Note: README.md` in the API's own
 * fixture), and a document whose `indexed_at` predates an edit is stale.
 *
 * ## `documents_indexed == 0` is a success
 *
 * `POST /api/knowledge/reindex` is idempotent (contract §9): every document is
 * re-hashed and only the ones whose hash changed are re-embedded. So the second
 * run on an unchanged tree returns `documents_indexed == 0`, and that is the
 * answer an operator sees most often, because pressing the button again is the
 * natural thing to do when nothing appears to happen.
 *
 * Saying so is the one piece of copy on this screen that matters. An interface
 * that lets `0` be read as failure has misled the operator about a working
 * system, and the misleading version is the one that gets written by default --
 * a bare `0` next to a button labelled "reindex" reads as nothing happening.
 * `reindex-button.tsx` states the zero case explicitly, in the same tone as the
 * non-zero case rather than as a warning, and distinguishes it from
 * `documents_seen == 0`, which is a genuinely empty tree and a real problem.
 */
export default async function KnowledgePage() {
  const knowledge = await loadKnowledge();
  const docs = knowledge.data?.items ?? [];
  const chunks = docs.reduce((total, doc) => total + doc.chunk_count, 0);
  const zeroChunk = docs.filter((doc) => doc.chunk_count === 0);

  return (
    <Page
      title="Knowledge"
      description="The documents retrieval can cite. A run can only cite a chunk that is indexed here, so this screen is where 'the agent found nothing' is explained."
    >
      <div className="space-y-6">
        <div className="grid gap-6 lg:grid-cols-3">
          <div className="lg:col-span-2">
            <Panel
              title="Indexed documents"
              aside={
                knowledge.data
                  ? `${knowledge.data.total} documents · ${chunks} chunks`
                  : undefined
              }
            >
              {knowledge.error ? (
                <ErrorPanel error={knowledge.error} title="Could not read the index" />
              ) : docs.length === 0 ? (
                <EmptyState
                  title="Nothing is indexed."
                  hint="The API reads knowledge/*.md beside src/ in the repository root. With nothing indexed, knowledge.search has nothing to retrieve and a run either abstains or answers from the model alone — press Reindex to ingest the tree."
                />
              ) : (
                <div className="overflow-x-auto">
                  <table className="w-full text-sm">
                    <thead>
                      <tr className="border-b border-neutral-200 text-left text-xs uppercase tracking-wide text-neutral-500 dark:border-neutral-800 dark:text-neutral-400">
                        <th className="py-2 pr-4 font-medium">Document</th>
                        <th className="py-2 pr-4 font-medium">Source</th>
                        <th className="py-2 pr-4 font-medium text-right">Chunks</th>
                        <th className="py-2 pr-4 font-medium">Indexed</th>
                        <th className="py-2 font-medium">Content hash</th>
                      </tr>
                    </thead>
                    <tbody>
                      {docs.map((doc) => (
                        <tr
                          key={doc.source}
                          className="border-b border-neutral-100 align-top last:border-0 dark:border-neutral-800"
                        >
                          <td className="py-2 pr-4 font-medium">{doc.title}</td>
                          <td className="py-2 pr-4 font-mono text-xs text-neutral-600 dark:text-neutral-400">
                            {doc.source}
                          </td>
                          <td className="py-2 pr-4 text-right tabular-nums">
                            {doc.chunk_count === 0 ? (
                              <span
                                className="text-amber-700 dark:text-amber-300"
                                title="Zero chunks: retrieval cannot cite this document."
                              >
                                0
                              </span>
                            ) : (
                              doc.chunk_count
                            )}
                          </td>
                          <td className="py-2 pr-4 text-xs text-neutral-600 dark:text-neutral-400">
                            <Time value={doc.indexed_at}>
                              {formatTimestamp(doc.indexed_at)}
                            </Time>
                          </td>
                          <td className="py-2 font-mono text-xs text-neutral-500 dark:text-neutral-400">
                            {doc.content_hash.slice(0, 12)}
                          </td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </div>
              )}
            </Panel>
          </div>

          <div className="space-y-6">
            <Panel title="Reindex">
              <ReindexButton />
            </Panel>

            {zeroChunk.length > 0 ? (
              <Panel title="Documents with no chunks">
                <ul className="space-y-1 text-sm">
                  {zeroChunk.map((doc) => (
                    <li key={doc.source} className="font-mono text-xs">
                      {doc.source}
                    </li>
                  ))}
                </ul>
                <p className="mt-2 text-xs text-neutral-600 dark:text-neutral-400">
                  A zero-chunk document is indexed as a row but has nothing to retrieve, so
                  retrieval cannot cite it. That is not an error the API reports &mdash; it is
                  visible only here and in a run that returns no sources.
                </p>
              </Panel>
            ) : null}
          </div>
        </div>
      </div>
    </Page>
  );
}
