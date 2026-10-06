/**
 * API types, GENERATED -- do not edit.
 *
 * Produced by `npm run generate:types`, which builds the FastAPI app in
 * process (no database, no running server) and feeds its OpenAPI document to
 * `openapi-typescript`. The source of truth is
 * `src/opspilot/api/schemas.py`; this file is a projection of it.
 *
 * Editing this file by hand is undone by the next regeneration, and
 * `npm run check:types` fails the moment the committed copy no longer matches
 * the live schema -- so a hand-edit cannot survive as a silent divergence.
 *
 * Regenerate:  npm run generate:types
 * Verify:      npm run check:types
 */

export interface paths {
    "/api/approvals": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        /**
         * List Approvals
         * @description List approvals, defaulting to the pending inbox (contract §5).
         *
         *     Raises:
         *         ApiError: 400 ``validation_error`` if ``status`` is not an
         *             ``ApprovalStatus``.
         */
        get: operations["list_approvals_api_approvals_get"];
        put?: never;
        post?: never;
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/api/approvals/{approval_id}": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        /**
         * Get Approval
         * @description One approval, with the arguments as shown (contract §1).
         *
         *     Raises:
         *         ApiError: 404 ``approval_not_found``.
         */
        get: operations["get_approval_api_approvals__approval_id__get"];
        put?: never;
        post?: never;
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/api/approvals/{approval_id}/approve": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        get?: never;
        put?: never;
        /**
         * Approve
         * @description Record approval and re-queue the run: ``WAITING_APPROVAL -> EXECUTING``.
         *
         *     The run is flipped to a claimable state and the worker picks it up on its next
         *     poll. This handler does **not** execute the tool call.
         *
         *     Args:
         *         approval_id: The approval to decide.
         *         approvals: The approval store port.
         *         runs: The run store port, used to read and move the parked run.
         *         request: Optional body carrying ``decided_by`` and a ``note``.
         *         operator: The authenticated operator id, the default ``decided_by``.
         *
         *     Returns:
         *         The decided approval and the transitioned run.
         *
         *     Raises:
         *         ApiError: 409 ``approval_already_decided`` if it was already decided;
         *             409 ``run_not_awaiting_approval`` if the run is not parked; 404 if
         *             the approval does not exist.
         */
        post: operations["approve_api_approvals__approval_id__approve_post"];
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/api/approvals/{approval_id}/reject": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        get?: never;
        put?: never;
        /**
         * Reject
         * @description Record rejection and re-queue the run for an escalation reply.
         *
         *     The transition is ``WAITING_APPROVAL -> RESPONDING``, **not** ``FAILED``: a
         *     rejected refund is the workflow working, and the run goes on to produce an
         *     escalation reply (``docs/agent-state-machine.md`` §3).
         */
        post: operations["reject_api_approvals__approval_id__reject_post"];
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/api/knowledge": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        /**
         * List Documents
         * @description List indexed documents and their chunk counts (contract §1).
         *
         *     Reads through the optional ``knowledge_store`` bound on ``app.state`` when the
         *     app factory provides one; otherwise returns an empty page rather than a 500,
         *     because the operator surface should not be the thing that takes the API down.
         */
        get: operations["list_documents_api_knowledge_get"];
        put?: never;
        post?: never;
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/api/knowledge/reindex": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        get?: never;
        put?: never;
        /**
         * Reindex
         * @description Re-ingest ``knowledge/`` -- idempotent, returns counts (contract §9).
         *
         *     The work goes through an ingestion callable bound on ``app.state`` when the
         *     app factory has one (it holds the embedder and the vector store, which are the
         *     expensive and deployment-specific pieces). When nothing is bound, the endpoint
         *     reports the documents it can see with zero indexed, rather than pretending a
         *     reindex happened.
         */
        post: operations["reindex_api_knowledge_reindex_post"];
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/api/runs": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        /**
         * List Runs
         * @description List runs, optionally filtered by status (contract §1, §10).
         *
         *     Raises:
         *         ApiError: 400 ``validation_error`` if ``status`` is not a ``RunStatus``.
         */
        get: operations["list_runs_api_runs_get"];
        put?: never;
        /**
         * Create Run
         * @description Enqueue a new run for an existing ticket (contract §9).
         *
         *     Re-running an investigation is a legitimate action, so this always creates a
         *     new run rather than deduplicating.
         *
         *     Raises:
         *         ApiError: 404 ``ticket_not_found`` if the ticket does not exist.
         */
        post: operations["create_run_api_runs_post"];
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/api/runs/{run_id}": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        /**
         * Get Run
         * @description The run-detail payload the dashboard renders (contract §3).
         *
         *     ``pending_approval`` is populated only when the run is parked in
         *     ``WAITING_APPROVAL``; ``customer_reply`` only from a completed run's response
         *     step. Both are fetched through optional store methods so a store that does
         *     not offer them still serves the run.
         *
         *     Raises:
         *         ApiError: 404 ``run_not_found`` if there is no such run.
         */
        get: operations["get_run_api_runs__run_id__get"];
        put?: never;
        post?: never;
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/api/runs/{run_id}/trace": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        /**
         * Get Run Trace
         * @description The ordered timeline only, without tool arguments or model payloads.
         *
         *     Each step is projected to a human-readable ``label`` and a short ``detail``
         *     server-side, so the client renders without deciding what a step means.
         *
         *     Raises:
         *         ApiError: 404 ``run_not_found`` if there is no such run.
         */
        get: operations["get_run_trace_api_runs__run_id__trace_get"];
        put?: never;
        post?: never;
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/api/tickets": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        /**
         * List Tickets
         * @description List tickets, newest first (contract §10).
         *
         *     Raises:
         *         ApiError: 500 if the store cannot list -- it is not required to by the
         *             ``TicketStore`` port, so this endpoint degrades to an empty page
         *             rather than pretending.
         */
        get: operations["list_tickets_api_tickets_get"];
        put?: never;
        /**
         * Create Ticket
         * @description Enqueue a ticket and its run, in one transaction, and return 201.
         *
         *     The ticket insert and the ``RECEIVED`` run insert are one unit of work: both
         *     rows land or neither does. The store's transaction boundary is what makes
         *     that true -- a ticket with no run would be silently never investigated, which
         *     is worse than a failed request the client retries.
         *
         *     Args:
         *         request: The ticket to create.
         *         tickets: The ticket store port.
         *         runs: The run store port.
         *         settings: Process settings, for the model provider/name the run records.
         *
         *     Returns:
         *         The nested ``ticket`` + ``run`` shape, with the run in ``received``.
         */
        post: operations["create_ticket_api_tickets_post"];
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/api/tickets/{ticket_id}": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        /**
         * Get Ticket
         * @description One ticket with its runs.
         *
         *     Raises:
         *         ApiError: 404 ``ticket_not_found`` if there is no such ticket.
         */
        get: operations["get_ticket_api_tickets__ticket_id__get"];
        put?: never;
        post?: never;
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/health": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        /**
         * Health
         * @description Liveness: the process is up. Touches nothing.
         */
        get: operations["health_health_get"];
        put?: never;
        post?: never;
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/ready": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        /**
         * Ready
         * @description Readiness: check the database and that migrations are at head.
         *
         *     Each check is named in the body so a 503 states which one failed rather than a
         *     bare "not ready". The checker is bound on ``app.state`` by the app factory
         *     (``readiness_check``); with none bound this reports both checks as ``ok``,
         *     which is honest for an app that has no database wired yet.
         *
         *     Returns:
         *         ``200`` with ``{"status":"ready","checks":{...}}`` when every check
         *         passes, or ``503`` with the failing check named.
         */
        get: operations["ready_ready_get"];
        put?: never;
        post?: never;
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
}
export type webhooks = Record<string, never>;
export interface components {
    schemas: {
        /**
         * ApprovalContext
         * @description The extra context shown beside a pending approval (contract §5).
         *
         *     Assembled from already-persisted data; it triggers no tool calls.
         */
        ApprovalContext: {
            /** Company */
            company?: string | null;
            /** Ticket Subject */
            ticket_subject?: string | null;
        };
        /**
         * ApprovalDecisionRequest
         * @description Body of ``POST /api/approvals/{id}/approve`` and ``/reject``.
         *
         *     The body may be empty; ``decided_by`` defaults to the operator identity
         *     resolved from the bearer token, and ``note`` is optional.
         */
        ApprovalDecisionRequest: {
            /** Decided By */
            decided_by?: string | null;
            /** Note */
            note?: string | null;
        };
        /**
         * ApprovalDecisionResponse
         * @description The 200 body of an approve/reject (contract §5).
         *
         *     ``run`` is the *transitioned* run -- ``executing`` after an approve,
         *     ``responding`` after a reject -- so the dashboard can update without a
         *     follow-up fetch.
         */
        ApprovalDecisionResponse: {
            /**
             * Decided At
             * Format: date-time
             */
            decided_at: string;
            /** Decided By */
            decided_by: string;
            /**
             * Id
             * Format: uuid
             */
            id: string;
            run: components["schemas"]["RunRef"];
            status: components["schemas"]["ApprovalStatus"];
        };
        /**
         * ApprovalListResponse
         * @description Body of ``GET /api/approvals`` (contract §5, §10).
         */
        ApprovalListResponse: {
            /** Items */
            items: components["schemas"]["ApprovalSummary"][];
            /** Total */
            total: number;
        };
        /**
         * ApprovalStatus
         * @description The decision state of an ``ApprovalRequest``.
         * @enum {string}
         */
        ApprovalStatus: "pending" | "approved" | "rejected" | "expired";
        /**
         * ApprovalSummary
         * @description An approval as shown in the approvals inbox (contract §5).
         */
        ApprovalSummary: {
            /** Arguments Snapshot */
            arguments_snapshot: {
                [key: string]: unknown;
            };
            context?: components["schemas"]["ApprovalContext"] | null;
            /**
             * Created At
             * Format: date-time
             */
            created_at: string;
            /** Decided At */
            decided_at?: string | null;
            /** Decided By */
            decided_by?: string | null;
            /**
             * Id
             * Format: uuid
             */
            id: string;
            /** Reason */
            reason: string;
            /** Risk Explanation */
            risk_explanation: string;
            /**
             * Run Id
             * Format: uuid
             */
            run_id: string;
            status: components["schemas"]["ApprovalStatus"];
            /**
             * Tool Call Id
             * Format: uuid
             */
            tool_call_id: string;
        };
        /**
         * CitationDetail
         * @description One cited knowledge chunk (contract §3).
         *
         *     ``chunk`` is ``"{document_slug}#{anchor}"`` -- a stable string a reader can
         *     grep for in ``knowledge/``.
         */
        CitationDetail: {
            /** Chunk */
            chunk: string;
            /** Document */
            document: string;
            /** Rank */
            rank: number;
            /** Score */
            score: number;
        };
        /**
         * CustomerReply
         * @description The customer-visible reply, set only at ``COMPLETED`` (contract §3).
         *
         *     ``escalated`` is declared rather than smuggled through ``extra="allow"``:
         *     it is the field that distinguishes "we could not complete this and a human
         *     is on it" from "here is your refund", and a declared field is one the
         *     OpenAPI document publishes and the dashboard's generated types carry. A
         *     field that only exists at runtime is a field a client cannot render.
         */
        CustomerReply: {
            /** Body */
            body: string;
            /**
             * Escalated
             * @default false
             */
            escalated: boolean;
        } & {
            [key: string]: unknown;
        };
        /**
         * ErrorBody
         * @description The inner ``error`` object of the single error envelope (contract §6).
         */
        ErrorBody: {
            /** Code */
            code: string;
            /** Details */
            details?: {
                [key: string]: unknown;
            } | null;
            /** Message */
            message: string;
        };
        /**
         * ErrorResponse
         * @description The one error shape returned by every failure, everywhere (contract §6).
         */
        ErrorResponse: {
            error: components["schemas"]["ErrorBody"];
        };
        /**
         * HealthResponse
         * @description Body of ``GET /health`` (contract §7). Liveness only.
         */
        HealthResponse: {
            /**
             * Status
             * @default ok
             */
            status: string;
            /** Version */
            version: string;
        };
        /**
         * KnowledgeDocumentSummary
         * @description One indexed document in ``GET /api/knowledge``.
         */
        KnowledgeDocumentSummary: {
            /** Chunk Count */
            chunk_count: number;
            /** Content Hash */
            content_hash: string;
            /**
             * Indexed At
             * Format: date-time
             */
            indexed_at: string;
            /** Source */
            source: string;
            /** Title */
            title: string;
        };
        /**
         * KnowledgeListResponse
         * @description Body of ``GET /api/knowledge``.
         */
        KnowledgeListResponse: {
            /** Items */
            items: components["schemas"]["KnowledgeDocumentSummary"][];
            /** Total */
            total: number;
        };
        /**
         * KnowledgeReindexResponse
         * @description Result of ``POST /api/knowledge/reindex`` (contract §9).
         *
         *     ``documents_seen`` counts every file found; ``documents_indexed`` counts only
         *     those whose ``content_hash`` changed and were re-embedded, so a repeat call
         *     returns ``documents_indexed == 0`` on an unchanged tree.
         */
        KnowledgeReindexResponse: {
            /** Chunks Written */
            chunks_written: number;
            /** Documents Indexed */
            documents_indexed: number;
            /** Documents Seen */
            documents_seen: number;
        };
        /**
         * PendingApproval
         * @description The approver-visible payload nested inside a parked run (contract §3).
         */
        PendingApproval: {
            /** Arguments Snapshot */
            arguments_snapshot: {
                [key: string]: unknown;
            };
            /**
             * Created At
             * Format: date-time
             */
            created_at: string;
            /**
             * Id
             * Format: uuid
             */
            id: string;
            /** Reason */
            reason: string;
            /** Risk Explanation */
            risk_explanation: string;
            status: components["schemas"]["ApprovalStatus"];
            /**
             * Tool Call Id
             * Format: uuid
             */
            tool_call_id: string;
        };
        /**
         * Permission
         * @description How a tool may execute. Three levels, not five -- see §1 of the spec.
         * @enum {string}
         */
        Permission: "read" | "safe_write" | "high_risk_write";
        /**
         * ReadyResponse
         * @description Body of ``GET /ready`` (contract §7).
         *
         *     ``checks`` names each probe so a 503 body states which one failed rather than
         *     a bare "not ready".
         */
        ReadyResponse: {
            /** Checks */
            checks: {
                [key: string]: string;
            };
            /** Status */
            status: string;
        };
        /**
         * RunCreateRequest
         * @description Body of ``POST /api/runs`` -- enqueue a run for an existing ticket.
         */
        RunCreateRequest: {
            /**
             * Ticket Id
             * Format: uuid
             */
            ticket_id: string;
        };
        /**
         * RunDetail
         * @description Body of ``GET /api/runs/{run_id}`` -- the run-detail screen (contract §3).
         *
         *     ``pending_approval`` is present only when the run is parked; ``customer_reply``
         *     is set only at ``COMPLETED``.
         */
        RunDetail: {
            /** Citations */
            citations?: components["schemas"]["CitationDetail"][];
            /** Completed At */
            completed_at?: string | null;
            /**
             * Created At
             * Format: date-time
             */
            created_at: string;
            customer_reply?: components["schemas"]["CustomerReply"] | null;
            /** Failure Reason */
            failure_reason?: string | null;
            /**
             * Id
             * Format: uuid
             */
            id: string;
            /** Model Name */
            model_name: string;
            /** Model Provider */
            model_provider: string;
            pending_approval?: components["schemas"]["PendingApproval"] | null;
            /** Started At */
            started_at?: string | null;
            status: components["schemas"]["RunStatus"];
            /** Steps */
            steps?: components["schemas"]["StepDetail"][];
            /**
             * Ticket Id
             * Format: uuid
             */
            ticket_id: string;
            /** Tool Calls */
            tool_calls?: components["schemas"]["ToolCallDetail"][];
        };
        /**
         * RunListResponse
         * @description Body of ``GET /api/runs`` (contract §10).
         */
        RunListResponse: {
            /** Items */
            items: components["schemas"]["RunSummary"][];
            /** Total */
            total: number;
        };
        /**
         * RunRef
         * @description A run reference embedded in the ticket-create response (``{id, status}``).
         */
        RunRef: {
            /**
             * Created At
             * Format: date-time
             */
            created_at: string;
            /**
             * Id
             * Format: uuid
             */
            id: string;
            status: components["schemas"]["RunStatus"];
        };
        /**
         * RunStatus
         * @description The lifecycle of an agent run.
         *
         *     ``WAITING_APPROVAL`` is the one non-terminal state in which the worker is
         *     *not* holding the row -- every other non-terminal state is claim-and-work.
         * @enum {string}
         */
        RunStatus: "received" | "classifying" | "retrieving" | "planning" | "executing" | "waiting_approval" | "responding" | "completed" | "failed";
        /**
         * RunSummary
         * @description A run as listed by ``GET /api/runs``.
         */
        RunSummary: {
            /** Completed At */
            completed_at?: string | null;
            /**
             * Created At
             * Format: date-time
             */
            created_at: string;
            /** Failure Reason */
            failure_reason?: string | null;
            /**
             * Id
             * Format: uuid
             */
            id: string;
            /** Model Name */
            model_name: string;
            /** Model Provider */
            model_provider: string;
            /** Started At */
            started_at?: string | null;
            status: components["schemas"]["RunStatus"];
            /**
             * Ticket Id
             * Format: uuid
             */
            ticket_id: string;
        };
        /**
         * StepDetail
         * @description One execution-timeline step in the run-detail payload (contract §3).
         */
        StepDetail: {
            /** Latency Ms */
            latency_ms?: number | null;
            /** Output */
            output?: {
                [key: string]: unknown;
            } | null;
            /** Sequence */
            sequence: number;
            /**
             * Started At
             * Format: date-time
             */
            started_at: string;
            /** Step Type */
            step_type: string;
        };
        /**
         * TicketCreateRequest
         * @description Body of ``POST /api/tickets``.
         */
        TicketCreateRequest: {
            /** Body */
            body: string;
            /** Customer Email */
            customer_email: string;
            /** External Id */
            external_id?: string | null;
            /** Subject */
            subject: string;
        };
        /**
         * TicketCreateResponse
         * @description The created ticket and the run enqueued for it (contract §2).
         *
         *     The nested ``ticket`` + ``run`` shape is deliberate: the client gets both ids
         *     and the run's starting status in one response, so it can begin polling
         *     ``GET /api/runs/{id}`` without a second request.
         */
        TicketCreateResponse: {
            run: components["schemas"]["RunRef"];
            ticket: components["schemas"]["TicketDetail"];
        };
        /**
         * TicketDetail
         * @description A ticket as returned by ``POST /api/tickets`` and ``GET /api/tickets/{id}``.
         */
        TicketDetail: {
            /** Body */
            body: string;
            /**
             * Created At
             * Format: date-time
             */
            created_at: string;
            /** Customer Email */
            customer_email: string;
            /** External Id */
            external_id?: string | null;
            /**
             * Id
             * Format: uuid
             */
            id: string;
            /** Subject */
            subject: string;
        };
        /**
         * TicketDetailResponse
         * @description Body of ``GET /api/tickets/{ticket_id}``: the ticket and its runs.
         */
        TicketDetailResponse: {
            /** Runs */
            runs: components["schemas"]["RunRef"][];
            ticket: components["schemas"]["TicketDetail"];
        };
        /**
         * TicketListResponse
         * @description Body of ``GET /api/tickets`` (contract §10).
         */
        TicketListResponse: {
            /** Items */
            items: components["schemas"]["TicketDetail"][];
            /** Total */
            total: number;
        };
        /**
         * ToolCallDetail
         * @description One tool call in a run's detail payload (contract §3).
         */
        ToolCallDetail: {
            /** Arguments */
            arguments: {
                [key: string]: unknown;
            };
            /** Error */
            error?: string | null;
            /**
             * Id
             * Format: uuid
             */
            id: string;
            /** Idempotency Key */
            idempotency_key?: string | null;
            /** Latency Ms */
            latency_ms?: number | null;
            permission: components["schemas"]["Permission"];
            /** Result */
            result?: {
                [key: string]: unknown;
            } | null;
            status: components["schemas"]["ToolCallStatus"];
            /** Tool Name */
            tool_name: string;
        };
        /**
         * ToolCallStatus
         * @description The lifecycle of a single tool call (the ``tool_calls.status`` column).
         * @enum {string}
         */
        ToolCallStatus: "proposed" | "rejected" | "awaiting_approval" | "executed" | "failed";
        /**
         * TraceResponse
         * @description Body of ``GET /api/runs/{run_id}/trace`` (contract §4).
         */
        TraceResponse: {
            /**
             * Run Id
             * Format: uuid
             */
            run_id: string;
            /** Steps */
            steps?: components["schemas"]["TraceStep"][];
        };
        /**
         * TraceStep
         * @description One step in the cheap trace payload (contract §4).
         *
         *     ``label`` is already human-readable and ``detail`` is a short summary string,
         *     both assembled server-side, so rendering the timeline requires no client-side
         *     logic beyond ordering.
         */
        TraceStep: {
            /**
             * At
             * Format: date-time
             */
            at: string;
            /** Detail */
            detail: string;
            /** Label */
            label: string;
            /** Latency Ms */
            latency_ms?: number | null;
            /** Sequence */
            sequence: number;
            /** Step Type */
            step_type: string;
        };
    };
    responses: never;
    parameters: never;
    requestBodies: never;
    headers: never;
    pathItems: never;
}
export type $defs = Record<string, never>;
export interface operations {
    list_approvals_api_approvals_get: {
        parameters: {
            query?: {
                status?: string;
                limit?: number;
                offset?: number;
            };
            header?: {
                authorization?: string | null;
            };
            path?: never;
            cookie?: never;
        };
        requestBody?: never;
        responses: {
            /** @description Successful Response */
            200: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["ApprovalListResponse"];
                };
            };
            /** @description Bad request (``validation_error``) */
            400: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["ErrorResponse"];
                };
            };
            /** @description Missing or invalid bearer token (``unauthorized``) */
            401: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["ErrorResponse"];
                };
            };
            /** @description No such resource (entity-specific code) */
            404: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["ErrorResponse"];
                };
            };
            /** @description Conflicting state (``*_already_decided`` and friends) */
            409: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["ErrorResponse"];
                };
            };
            /** @description Schema validation failed (``validation_error``) */
            422: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["ErrorResponse"];
                };
            };
        };
    };
    get_approval_api_approvals__approval_id__get: {
        parameters: {
            query?: never;
            header?: {
                authorization?: string | null;
            };
            path: {
                approval_id: string;
            };
            cookie?: never;
        };
        requestBody?: never;
        responses: {
            /** @description Successful Response */
            200: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["ApprovalSummary"];
                };
            };
            /** @description Bad request (``validation_error``) */
            400: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["ErrorResponse"];
                };
            };
            /** @description Missing or invalid bearer token (``unauthorized``) */
            401: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["ErrorResponse"];
                };
            };
            /** @description No such resource (entity-specific code) */
            404: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["ErrorResponse"];
                };
            };
            /** @description Conflicting state (``*_already_decided`` and friends) */
            409: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["ErrorResponse"];
                };
            };
            /** @description Schema validation failed (``validation_error``) */
            422: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["ErrorResponse"];
                };
            };
        };
    };
    approve_api_approvals__approval_id__approve_post: {
        parameters: {
            query?: never;
            header?: {
                authorization?: string | null;
            };
            path: {
                approval_id: string;
            };
            cookie?: never;
        };
        requestBody?: {
            content: {
                "application/json": components["schemas"]["ApprovalDecisionRequest"] | null;
            };
        };
        responses: {
            /** @description Successful Response */
            200: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["ApprovalDecisionResponse"];
                };
            };
            /** @description Bad request (``validation_error``) */
            400: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["ErrorResponse"];
                };
            };
            /** @description Missing or invalid bearer token (``unauthorized``) */
            401: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["ErrorResponse"];
                };
            };
            /** @description No such resource (entity-specific code) */
            404: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["ErrorResponse"];
                };
            };
            /** @description Conflicting state (``*_already_decided`` and friends) */
            409: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["ErrorResponse"];
                };
            };
            /** @description Schema validation failed (``validation_error``) */
            422: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["ErrorResponse"];
                };
            };
        };
    };
    reject_api_approvals__approval_id__reject_post: {
        parameters: {
            query?: never;
            header?: {
                authorization?: string | null;
            };
            path: {
                approval_id: string;
            };
            cookie?: never;
        };
        requestBody?: {
            content: {
                "application/json": components["schemas"]["ApprovalDecisionRequest"] | null;
            };
        };
        responses: {
            /** @description Successful Response */
            200: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["ApprovalDecisionResponse"];
                };
            };
            /** @description Bad request (``validation_error``) */
            400: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["ErrorResponse"];
                };
            };
            /** @description Missing or invalid bearer token (``unauthorized``) */
            401: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["ErrorResponse"];
                };
            };
            /** @description No such resource (entity-specific code) */
            404: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["ErrorResponse"];
                };
            };
            /** @description Conflicting state (``*_already_decided`` and friends) */
            409: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["ErrorResponse"];
                };
            };
            /** @description Schema validation failed (``validation_error``) */
            422: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["ErrorResponse"];
                };
            };
        };
    };
    list_documents_api_knowledge_get: {
        parameters: {
            query?: never;
            header?: {
                authorization?: string | null;
            };
            path?: never;
            cookie?: never;
        };
        requestBody?: never;
        responses: {
            /** @description Successful Response */
            200: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["KnowledgeListResponse"];
                };
            };
            /** @description Bad request (``validation_error``) */
            400: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["ErrorResponse"];
                };
            };
            /** @description Missing or invalid bearer token (``unauthorized``) */
            401: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["ErrorResponse"];
                };
            };
            /** @description No such resource (entity-specific code) */
            404: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["ErrorResponse"];
                };
            };
            /** @description Conflicting state (``*_already_decided`` and friends) */
            409: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["ErrorResponse"];
                };
            };
            /** @description Schema validation failed (``validation_error``) */
            422: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["ErrorResponse"];
                };
            };
        };
    };
    reindex_api_knowledge_reindex_post: {
        parameters: {
            query?: never;
            header?: {
                authorization?: string | null;
            };
            path?: never;
            cookie?: never;
        };
        requestBody?: never;
        responses: {
            /** @description Successful Response */
            200: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["KnowledgeReindexResponse"];
                };
            };
            /** @description Bad request (``validation_error``) */
            400: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["ErrorResponse"];
                };
            };
            /** @description Missing or invalid bearer token (``unauthorized``) */
            401: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["ErrorResponse"];
                };
            };
            /** @description No such resource (entity-specific code) */
            404: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["ErrorResponse"];
                };
            };
            /** @description Conflicting state (``*_already_decided`` and friends) */
            409: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["ErrorResponse"];
                };
            };
            /** @description Schema validation failed (``validation_error``) */
            422: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["ErrorResponse"];
                };
            };
        };
    };
    list_runs_api_runs_get: {
        parameters: {
            query?: {
                status?: string | null;
                limit?: number;
                offset?: number;
            };
            header?: {
                authorization?: string | null;
            };
            path?: never;
            cookie?: never;
        };
        requestBody?: never;
        responses: {
            /** @description Successful Response */
            200: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["RunListResponse"];
                };
            };
            /** @description Bad request (``validation_error``) */
            400: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["ErrorResponse"];
                };
            };
            /** @description Missing or invalid bearer token (``unauthorized``) */
            401: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["ErrorResponse"];
                };
            };
            /** @description No such resource (entity-specific code) */
            404: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["ErrorResponse"];
                };
            };
            /** @description Conflicting state (``*_already_decided`` and friends) */
            409: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["ErrorResponse"];
                };
            };
            /** @description Schema validation failed (``validation_error``) */
            422: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["ErrorResponse"];
                };
            };
        };
    };
    create_run_api_runs_post: {
        parameters: {
            query?: never;
            header?: {
                authorization?: string | null;
            };
            path?: never;
            cookie?: never;
        };
        requestBody: {
            content: {
                "application/json": components["schemas"]["RunCreateRequest"];
            };
        };
        responses: {
            /** @description Successful Response */
            201: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["RunRef"];
                };
            };
            /** @description Bad request (``validation_error``) */
            400: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["ErrorResponse"];
                };
            };
            /** @description Missing or invalid bearer token (``unauthorized``) */
            401: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["ErrorResponse"];
                };
            };
            /** @description No such resource (entity-specific code) */
            404: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["ErrorResponse"];
                };
            };
            /** @description Conflicting state (``*_already_decided`` and friends) */
            409: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["ErrorResponse"];
                };
            };
            /** @description Schema validation failed (``validation_error``) */
            422: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["ErrorResponse"];
                };
            };
        };
    };
    get_run_api_runs__run_id__get: {
        parameters: {
            query?: never;
            header?: {
                authorization?: string | null;
            };
            path: {
                run_id: string;
            };
            cookie?: never;
        };
        requestBody?: never;
        responses: {
            /** @description Successful Response */
            200: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["RunDetail"];
                };
            };
            /** @description Bad request (``validation_error``) */
            400: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["ErrorResponse"];
                };
            };
            /** @description Missing or invalid bearer token (``unauthorized``) */
            401: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["ErrorResponse"];
                };
            };
            /** @description No such resource (entity-specific code) */
            404: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["ErrorResponse"];
                };
            };
            /** @description Conflicting state (``*_already_decided`` and friends) */
            409: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["ErrorResponse"];
                };
            };
            /** @description Schema validation failed (``validation_error``) */
            422: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["ErrorResponse"];
                };
            };
        };
    };
    get_run_trace_api_runs__run_id__trace_get: {
        parameters: {
            query?: never;
            header?: {
                authorization?: string | null;
            };
            path: {
                run_id: string;
            };
            cookie?: never;
        };
        requestBody?: never;
        responses: {
            /** @description Successful Response */
            200: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["TraceResponse"];
                };
            };
            /** @description Bad request (``validation_error``) */
            400: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["ErrorResponse"];
                };
            };
            /** @description Missing or invalid bearer token (``unauthorized``) */
            401: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["ErrorResponse"];
                };
            };
            /** @description No such resource (entity-specific code) */
            404: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["ErrorResponse"];
                };
            };
            /** @description Conflicting state (``*_already_decided`` and friends) */
            409: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["ErrorResponse"];
                };
            };
            /** @description Schema validation failed (``validation_error``) */
            422: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["ErrorResponse"];
                };
            };
        };
    };
    list_tickets_api_tickets_get: {
        parameters: {
            query?: {
                limit?: number;
                offset?: number;
            };
            header?: {
                authorization?: string | null;
            };
            path?: never;
            cookie?: never;
        };
        requestBody?: never;
        responses: {
            /** @description Successful Response */
            200: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["TicketListResponse"];
                };
            };
            /** @description Bad request (``validation_error``) */
            400: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["ErrorResponse"];
                };
            };
            /** @description Missing or invalid bearer token (``unauthorized``) */
            401: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["ErrorResponse"];
                };
            };
            /** @description No such resource (entity-specific code) */
            404: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["ErrorResponse"];
                };
            };
            /** @description Conflicting state (``*_already_decided`` and friends) */
            409: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["ErrorResponse"];
                };
            };
            /** @description Schema validation failed (``validation_error``) */
            422: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["ErrorResponse"];
                };
            };
        };
    };
    create_ticket_api_tickets_post: {
        parameters: {
            query?: never;
            header?: {
                authorization?: string | null;
            };
            path?: never;
            cookie?: never;
        };
        requestBody: {
            content: {
                "application/json": components["schemas"]["TicketCreateRequest"];
            };
        };
        responses: {
            /** @description Successful Response */
            201: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["TicketCreateResponse"];
                };
            };
            /** @description Bad request (``validation_error``) */
            400: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["ErrorResponse"];
                };
            };
            /** @description Missing or invalid bearer token (``unauthorized``) */
            401: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["ErrorResponse"];
                };
            };
            /** @description No such resource (entity-specific code) */
            404: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["ErrorResponse"];
                };
            };
            /** @description Conflicting state (``*_already_decided`` and friends) */
            409: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["ErrorResponse"];
                };
            };
            /** @description Schema validation failed (``validation_error``) */
            422: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["ErrorResponse"];
                };
            };
        };
    };
    get_ticket_api_tickets__ticket_id__get: {
        parameters: {
            query?: never;
            header?: {
                authorization?: string | null;
            };
            path: {
                ticket_id: string;
            };
            cookie?: never;
        };
        requestBody?: never;
        responses: {
            /** @description Successful Response */
            200: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["TicketDetailResponse"];
                };
            };
            /** @description Bad request (``validation_error``) */
            400: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["ErrorResponse"];
                };
            };
            /** @description Missing or invalid bearer token (``unauthorized``) */
            401: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["ErrorResponse"];
                };
            };
            /** @description No such resource (entity-specific code) */
            404: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["ErrorResponse"];
                };
            };
            /** @description Conflicting state (``*_already_decided`` and friends) */
            409: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["ErrorResponse"];
                };
            };
            /** @description Schema validation failed (``validation_error``) */
            422: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["ErrorResponse"];
                };
            };
        };
    };
    health_health_get: {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        requestBody?: never;
        responses: {
            /** @description Successful Response */
            200: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["HealthResponse"];
                };
            };
        };
    };
    ready_ready_get: {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        requestBody?: never;
        responses: {
            /** @description Successful Response */
            200: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["ReadyResponse"];
                };
            };
        };
    };
}
