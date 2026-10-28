import { runtimeVariable } from "../../lib/runtime-env";
import { corpusApiFetch } from "../../lib/corpus-api";
import { isAuthResponse, requireApiUser } from "../../lib/auth-server";
import type { AppliedSearch } from "../../lib/retrieval-execution";
import { normalizedKey } from "../../lib/text-grounding";

type CorpusKind = "filing" | "announcement" | "macro";
type FilingCadence = "annual" | "quarterly" | "any";
type FilingCadenceBasis = "explicit" | "evidence_structure" | "not_applicable";
type EntityRole = "required" | "supporting";
type TemporalMode = "bounded" | "latest" | "recent" | "all";
type Workflow = "browse" | "financial_model";

export interface Entity {
  ticker: string;
  company_name: string;
  industry: string;
}

interface RoutedEntity {
  ticker: string;
  company_name: string;
  mention: string;
  confidence: number;
  role: EntityRole;
}

interface ModelFieldQueries {
  key: string;
  summary: string;
  text: string;
  heading: string;
}

interface ModelFacet {
  label: string;
  evidence_need: string;
  lexical_query: string;
  field_queries: ModelFieldQueries;
  corpora: CorpusKind[];
  corpus_mode: "explicit" | "inferred";
  filing_cadence: FilingCadence;
  filing_cadence_basis: FilingCadenceBasis;
  temporal_mode: TemporalMode;
}

export interface ModelRoute {
  literal_query: string;
  evidence_needs: string[];
  search_facets: ModelFacet[];
  entities: Array<{
    ticker: string;
    mention: string;
    confidence: number;
    role: EntityRole;
  }>;
  industries: string[];
  unresolved_entities: string[];
  from_year: number | null;
  to_year: number | null;
  temporal_mode: TemporalMode;
  time_expression: string | null;
  continuation: boolean;
  corpora: CorpusKind[];
  corpus_mode: "explicit" | "inferred";
  source_expression: string | null;
  filing_cadence: FilingCadence;
  filing_cadence_basis: FilingCadenceBasis;
  workflow: Workflow;
  query_pack: null;
  interpretation: string;
}

export interface PriorSearchContext {
  naturalQuery?: string;
  tickers?: string[];
  industries?: string[];
  years?: number[];
  corpora?: CorpusKind[];
  filingCadence?: FilingCadence;
  temporalMode?: TemporalMode;
  latest?: boolean;
  searchFacets?: Array<{ label?: string; lexicalQuery?: string }>;
}

export class SearchRouterError extends Error {
  readonly status: number;
  readonly detail: string;

  constructor(
    message: string,
    status: number = 502,
    detail: string = message,
  ) {
    super(message);
    this.status = status;
    this.detail = detail;
  }
}

const ROUTER_MODEL = "gemini-3.5-flash-lite";
type RouterReasoningEffort = "minimal" | "low" | "medium" | "high";

interface RouteModelOptions {
  model?: string;
  reasoningEffort?: RouterReasoningEffort;
  timeoutMs?: number;
}
const ALLOWED_CORPORA = new Set<CorpusKind>(["filing", "announcement", "macro"]);
const ALLOWED_CADENCES = new Set<FilingCadence>(["annual", "quarterly", "any"]);
const ALLOWED_TEMPORAL_MODES = new Set<TemporalMode>([
  "bounded",
  "latest",
  "recent",
  "all",
]);
const FIELD_QUERY_SCHEMA = {
  type: "object",
  additionalProperties: false,
  properties: {
    key: {
      type: "string",
      description: "Two to six discriminative source-language words targeting the compact page Key; exclude entity, period, corpus, and generic document terms.",
    },
    summary: { type: "string", description: "Two to eight discriminative words targeting the page summary." },
    text: { type: "string", description: "Two to eight literal source words targeting original Markdown." },
    heading: { type: "string", description: "One to eight words targeting a likely page heading or title." },
  },
  required: ["key", "summary", "text", "heading"],
} as const;
const ROUTE_RESPONSE_FORMAT = {
  type: "json_schema",
  json_schema: {
    name: "retrieval_plan",
    strict: true,
    schema: {
      type: "object",
      additionalProperties: false,
      properties: {
        literal_query: { type: "string" },
        evidence_needs: {
          type: "array",
          minItems: 1,
          maxItems: 4,
          items: { type: "string" },
        },
        search_facets: {
          type: "array",
          minItems: 1,
          maxItems: 4,
          items: {
            type: "object",
            additionalProperties: false,
            properties: {
              label: { type: "string" },
              evidence_need: { type: "string" },
              lexical_query: { type: "string" },
              field_queries: FIELD_QUERY_SCHEMA,
              corpora: {
                type: "array",
                minItems: 1,
                maxItems: 1,
                items: { type: "string", enum: ["filing", "announcement", "macro"] },
              },
              corpus_mode: { type: "string", enum: ["explicit", "inferred"] },
              filing_cadence: {
                type: "string",
                enum: ["annual", "quarterly", "any"],
              },
              filing_cadence_basis: {
                type: "string",
                enum: ["explicit", "evidence_structure", "not_applicable"],
              },
              temporal_mode: {
                type: "string",
                enum: ["bounded", "latest", "recent", "all"],
              },
            },
            required: [
              "label", "evidence_need", "lexical_query",
              "field_queries", "corpora", "corpus_mode",
              "filing_cadence", "filing_cadence_basis", "temporal_mode",
            ],
          },
        },
        entities: {
          type: "array",
          items: {
            type: "object",
            additionalProperties: false,
            properties: {
              ticker: { type: "string" },
              mention: { type: "string" },
              confidence: { type: "number", minimum: 0, maximum: 1 },
              role: { type: "string", enum: ["required", "supporting"] },
            },
            required: ["ticker", "mention", "confidence", "role"],
          },
        },
        industries: { type: "array", items: { type: "string" } },
        unresolved_entities: { type: "array", items: { type: "string" } },
        from_year: { type: ["integer", "null"] },
        to_year: { type: ["integer", "null"] },
        temporal_mode: {
          type: "string",
          enum: ["bounded", "latest", "recent", "all"],
        },
        time_expression: { type: ["string", "null"] },
        continuation: { type: "boolean" },
        corpora: {
          type: "array",
          minItems: 1,
          items: { type: "string", enum: ["filing", "announcement", "macro"] },
          description: "Smallest sufficient corpus set after applying the evidence-location decision order.",
        },
        corpus_mode: { type: "string", enum: ["explicit", "inferred"] },
        source_expression: { type: ["string", "null"] },
        filing_cadence: { type: "string", enum: ["annual", "quarterly", "any"] },
        filing_cadence_basis: {
          type: "string",
          enum: ["explicit", "evidence_structure", "not_applicable"],
        },
        workflow: { type: "string", enum: ["browse", "financial_model"] },
        query_pack: { type: "null" },
        interpretation: { type: "string" },
      },
      required: [
        "literal_query", "evidence_needs",
        "search_facets", "entities", "industries", "unresolved_entities",
        "from_year", "to_year", "temporal_mode", "time_expression",
        "continuation", "corpora", "corpus_mode", "source_expression",
        "filing_cadence", "filing_cadence_basis",
        "workflow", "query_pack", "interpretation",
      ],
    },
  },
} as const;

function compactText(value: unknown, maxLength: number): string {
  let output = "";
  let pendingSpace = false;
  for (const character of String(value ?? "").normalize("NFKC")) {
    if (character.trim() === "") {
      pendingSpace = output.length > 0;
      continue;
    }
    if (pendingSpace && output.length < maxLength) output += " ";
    if (output.length < maxLength) output += character;
    pendingSpace = false;
    if (output.length >= maxLength) break;
  }
  return output.trim();
}

function isIntegerYear(value: unknown): value is number {
  return Number.isInteger(value) && Number(value) >= 1900 && Number(value) <= 2100;
}

function normalizeModelRoute(
  value: unknown,
  entityDirectory: Map<string, Entity>,
  industryKeys: Set<string>,
): ModelRoute {
  if (!value || typeof value !== "object") {
    throw new Error("The response must be a JSON object.");
  }
  const route = value as ModelRoute;
  if (!Array.isArray(route.search_facets) || route.search_facets.length === 0) {
    throw new Error("The response did not contain a search facet.");
  }

  const searchFacets = route.search_facets.slice(0, 4);
  const proposedEntities = Array.isArray(route.entities) ? route.entities : [];
  const entities = proposedEntities
    .map((candidate) => ({
      ...candidate,
      ticker: compactText(candidate.ticker, 20).toUpperCase(),
      mention: compactText(candidate.mention, 120),
    }))
    .filter((candidate) => entityDirectory.has(candidate.ticker));
  if (proposedEntities.length > 0 && entities.length === 0) {
    throw new Error("The router did not select a ticker from the corpus directory.");
  }
  const resolvedMentions = new Set(entities.map((candidate) => normalizedKey(candidate.mention)));
  const unresolvedEntities = (Array.isArray(route.unresolved_entities)
    ? route.unresolved_entities
    : [])
    .map((mention) => compactText(mention, 120))
    .filter((mention) => mention && !resolvedMentions.has(normalizedKey(mention)))
    .slice(0, 8);
  const industries = (Array.isArray(route.industries) ? route.industries : [])
    .map((industry) => compactText(industry, 120))
    .filter((industry) => industryKeys.has(normalizedKey(industry)))
    .slice(0, 4);
  const corpora = [...new Set(searchFacets.flatMap((facet) => facet.corpora))];
  const filingFacets = searchFacets.filter((facet) => facet.corpora.includes("filing"));
  const filingCadences = [...new Set(filingFacets.map((facet) => facet.filing_cadence))];
  const filingCadence = filingCadences.length === 1 ? filingCadences[0] : "any";
  const filingCadenceBases = [...new Set(
    filingFacets.map((facet) => facet.filing_cadence_basis),
  )];
  const filingCadenceBasis = filingFacets.length === 0
    ? "not_applicable"
    : filingCadenceBases.length === 1
      ? filingCadenceBases[0]
      : "evidence_structure";
  const corpusMode = searchFacets.some((facet) => facet.corpus_mode === "explicit")
    ? "explicit"
    : "inferred";

  return {
    ...route,
    evidence_needs: searchFacets.map((facet) => facet.evidence_need),
    search_facets: searchFacets,
    entities,
    industries,
    unresolved_entities: unresolvedEntities,
    corpora,
    corpus_mode: corpusMode,
    source_expression: corpusMode === "explicit" ? route.source_expression : null,
    filing_cadence: filingCadence,
    filing_cadence_basis: filingCadenceBasis,
    query_pack: null,
  };
}

function parseModelJson(content: string): unknown {
  return JSON.parse(content);
}

export async function routeWithModel(
  query: string,
  entities: Entity[],
  referenceDate: string,
  priorContext: PriorSearchContext | null,
  options: RouteModelOptions = {},
): Promise<ModelRoute> {
  const apiKey = await runtimeVariable("GEMINI_API_KEY");
  if (!apiKey) throw new SearchRouterError("Search routing is not configured.", 503);

  const directory = entities
    .map((entity) => `${entity.ticker}|${entity.company_name}|${entity.industry}`)
    .join("\n");
  const industries = [...new Set(
    entities.map((entity) => compactText(entity.industry, 120)).filter(Boolean),
  )].sort();
  const industryDirectory = industries.join("\n");
  const entityDirectory = new Map(entities.map((entity) => [entity.ticker, entity]));
  const industryKeys = new Set(industries.map(normalizedKey));
  const routerModel = compactText(options.model, 120) || ROUTER_MODEL;
  const reasoningEffort = options.reasoningEffort || "medium";
  const timeoutMs = Math.max(2_000, Math.min(20_000, options.timeoutMs || 12_000));
  const system = [
    "You are the sole search-planning controller for an authoritative Pakistani financial-document corpus.",
    "You do not answer the question and do not retrieve documents.",
    "Return a complete, executable retrieval plan. The application will validate your plan but will not repair, infer, or substitute missing fields.",
    "Plan in this order: identify the requested answer components; locate the authoritative corpus for each component; resolve entity and time scope; identify the most direct answer-bearing page type; then write field queries for that page.",
    "Optimize for pages that contain the evidence needed to answer the request, not pages that merely mention its subject.",
    "Evidence needs are only the independently answerable outputs the user requested. Do not invent background research, accounting treatment, historical context, or a second corpus as an additional evidence need.",
    "A facet is justified only when it targets a distinct requested output that normally lives on a different page or in a different corpus. Paraphrases and alternative search phrasings are retrieval lanes inside one facet, not additional facets.",
    "Every facet chooses exactly one authoritative corpus. Never combine filing, announcement, or macro inside one facet. If the user explicitly requests evidence from different corpora, express those as separate evidence needs and facets.",
    "If PRIOR SEARCH CONTEXT is present, decide whether this request continues it. Set continuation=true only when the request genuinely depends on the prior search.",
    "For a continuation, the application carries preserved prior companies and filters into execution. Do not repeat a prior entity in entities unless the current request explicitly names it again; entities contains only company spans present in the current request.",
    "Resolve every company to an exact ticker symbol in TICKER DIRECTORY.",
    "Use your judgment to map common market names, abbreviations, possessives, historical names, and minor spelling differences to the intended directory company when the identification is clear. The application will not perform alias repair after this call.",
    "For every company explicitly mentioned in the current request, preserve the shortest exact user span as mention and return the selected directory ticker with a confidence from 0 to 1.",
    "Assign role=required when the requested answer or comparison must retrieve that company's own source pages. Assign role=supporting when the company is a transaction counterparty, acquisition target, benchmark, or other named evidence object but the user did not ask for that company's own documents.",
    "Every company in a peer comparison is required. A supporting company does not widen source-document scope; preserve its name or source-language company name in the facet queries so the required company's page is about the correct counterparty or target.",
    "If the user asks what both sides disclosed or requests evidence from both companies, both are required rather than supporting.",
    "The ticker must exist in TICKER DIRECTORY and the mention must be quoted from the request. The mention does not need to match the formal company name.",
    "TICKER DIRECTORY does not define named-company group membership. Return a group phrase in unresolved_entities unless the user also names its member companies individually; never silently widen it to an industry.",
    "A category that directly maps to INDUSTRY DIRECTORY is industry scope, not an unresolved company or named-company group. Put it only in industries.",
    "Do not resolve ordinary topic words as ticker symbols. A ticker that is also a common word is a company only when the user clearly names that company.",
    "Use unresolved_entities only when the company genuinely cannot be identified confidently from the request and directory.",
    "literal_query is the shortest faithful expression of the evidence requested. Preserve the user's important evidence words while excluding company, date, and explicit source-scope terms handled by filters.",
    "Each facet's lexical_query is a compact BM25 query using likely authoritative-source vocabulary. Exclude company names, ticker symbols, dates, and filler words.",
    "Each page has a Query-facing compact Key and an original Markdown Value. The Key is a grounded description of the evidence actually present: relationships, actions, measurements, breakdowns, comparison periods, and likely analyst search phrasings. It is not a fixed taxonomy and it is not the page text.",
    "Searchable corpus map:",
    "- filing: annual and quarterly company reports, primary statements, notes, operating tables, accounting policies, governance pages, and management narrative. Scope by ticker or company industry, period, and explicit filing cadence.",
    "- announcement: dated PSX notices and attachments such as insider transactions, acquisitions, contracts, board meetings, closed periods, payouts, and other corporate events. Search title, compact summary, and notice body.",
    "- macro: economy-wide publications such as the Pakistan Economic Survey, SBP reports, and budget documents. Search publication chapter and page content.",
    "For filings, distinguish the direct disclosure from a neighboring parent section: a requested balance composition belongs on its specific note or line-item page; a requested primary statement belongs on the statement itself; a requested operating breakdown belongs on its table; and management explanation belongs in narrative discussion.",
    "For announcements, the answer-bearing page is normally the notice whose title or opening disclosure states the requested event. Do not add a filing facet unless the user separately requests later financial impact, accounting treatment, or report evidence.",
    "For macro research, target the specific chapter, subsection, table, or discussion that answers the indicator or policy question rather than a broad publication overview.",
    "Choose the corpus before writing retrieval queries. Apply this evidence-location decision order:",
    "1. A named publication or source class uses its explicit corpus.",
    "2. A dated exchange notice or corporate event uses announcement.",
    "3. Company or listed-industry operating, financial, governance, product, capacity, utilisation, margin, or management evidence uses filing.",
    "4. Economy-wide indicators, policy, and public-sector chapters use macro.",
    "Industry scope alone never implies macro. Macro is for economy-wide evidence, not an aggregation of company filings.",
    "When industries contains a directory industry and the source was not explicitly named, company-level or peer-comparison evidence must use filing only.",
    "Return field_queries inside every facet for four independent retrieval lanes. Every field query must be present, discriminative, and written in likely source vocabulary.",
    "First form a private description of the ideal answer-bearing page: its likely direct heading or notice title, the measurement or action it contains, and any qualifier that distinguishes it from adjacent pages. Derive all four field queries from that target.",
    "Use the resolved company's industry to choose source vocabulary when disclosure labels vary by sector, while preserving the user's requested concept.",
    "field_queries.key targets the compact page Key. Use two to six words expressing the requested evidence relationship; do not add company, date, corpus, or generic document words.",
    "The Key query must preserve the user's discriminating object or qualifier when it is not already handled by an entity or industry filter, such as a named product, Stage 3, industry sector, or acquisition target. Do not replace it with a broader neighboring concept.",
    "Before returning, audit every field_queries.key: each discriminating evidence object not represented by an entity, industry, period, cadence, or corpus filter must remain in that Key or a close source-language synonym.",
    "field_queries.summary uses two to eight words describing the exact evidence sought with close source-language vocabulary.",
    "field_queries.text uses two to eight literal labels, actions, measurements, and terms likely to appear together in original page Markdown.",
    "field_queries.heading uses two to eight words forming a discriminative filing heading, announcement title, or publication chapter.",
    "The heading query must target the most specific likely answer-bearing heading or title. Do not use a broad parent statement, risk, policy, or chapter heading when the request identifies a more specific note, table, event, measurement, or subsection.",
    "For quantitative requests, make the queries seek the page containing the requested amount, ratio, breakdown, comparison, or table—not a methodology, policy, risk overview, or generic discussion that uses the same words.",
    "For narrative requests, preserve the requested causal, outlook, strategy, progress, or risk relationship rather than converting it into a nearby numeric disclosure.",
    "Do not over-compress a technical disclosure into a generic parent concept. Preserve the words that distinguish the requested table from neighboring policy, methodology, risk, and overview pages.",
    "Use a compact source-language phrase, not a list of loosely related synonyms. The independent lanes provide breadth.",
    "Preserve the event, action, or measurement requested. Translate informal wording into likely source vocabulary without changing it into a neighboring document class.",
    "Use the most specific likely subsection or disclosure title that answers the request. A broad parent section is a weak key.",
    "If requested concepts likely live on different pages, return separate search_facets instead of forcing all concepts into one query.",
    "First enumerate one to four independently answerable evidence_needs. Different disclosures, tables, measurements, or document actions that cannot normally be answered from the same page are different needs.",
    "An enduring rule, policy, strategy, or narrative and its dated observed outcomes or events are separate evidence needs, even when the request connects them in one sentence.",
    "Do not collapse an additional requested measurement into the first evidence need. If evidence_needs has more than one item, return exactly one matching search facet for each item.",
    "Return exactly one facet for every evidence_need, including when there is only one need, and no more than four facets. Each facet must include label, evidence_need copied exactly from evidence_needs, lexical_query, all four field_queries, corpora, corpus_mode, filing_cadence, filing_cadence_basis, and temporal_mode.",
    "Scope every facet independently. A single request may need an annual note facet and a latest-any-cadence snapshot facet, or an all-period policy facet and a latest announcement facet.",
    "A facet uses temporal_mode=latest only for one newest snapshot, recent for several recent observations without a fixed year window, bounded for the route's requested year window, and all when that evidence need has no time scope.",
    "A facet may use latest, recent, or bounded only when the route has the same grounded temporal_mode. Other facets in that plan may use all.",
    "The facet plan is authoritative. The application derives route-level corpora, evidence needs, and aggregate filing cadence from the facets rather than rejecting a useful plan over duplicated summary fields.",
    "In a mixed-corpus plan, every announcement or macro facet must independently use filing_cadence=any and filing_cadence_basis=not_applicable, even when another facet uses an annual or quarterly filing cadence.",
    "Facets represent different evidence types, never different companies. A peer comparison of one metric is one facet searched across all resolved companies.",
    "Do not repeat company names, tickers, dates, or generic words such as report, page, information, and financial statements in field queries.",
    "corpora may contain only filing, announcement, or macro.",
    "Set corpus_mode=explicit only when the user explicitly names a source class or named publication. Otherwise set inferred.",
    "An inferred corpus is your authoritative source choice based on the evidence request. Choose the smallest sufficient source set; execution will not add omitted corpora.",
    "Use filing for recurring statements, notes, accounting policies, operating tables, and management narrative contained in company reports.",
    "Use announcement for exchange notices and dated corporate events such as transaction disclosures, board meetings, closed periods, corporate actions, and material information.",
    "A request for what a company disclosed about a proposed or completed corporate transaction is event evidence and must include announcement. Add filing only when the request also needs recurring accounting treatment, financial impact, or later report evidence.",
    "Use macro for economy-wide evidence and named public economic publications rather than company evidence.",
    "Do not include an adjacent corpus merely because it may contain related background. If the request genuinely needs distinct evidence from different source classes, create separate facets and scope each facet to its own sufficient corpus.",
    "If the user names a publication, regulator, document class, or source class, corpus_mode must be explicit and source_expression must quote that exact source phrase.",
    "Examples of named source phrases include Economic Survey, PSX announcements, annual report, and quarterly filing. These examples illustrate the general named-source rule.",
    "If no source is named, corpus_mode must be inferred and source_expression must be null.",
    "Each facet may use corpus_mode=explicit only when the facet has an explicit source scope.",
    "Use filing cadence as an evidence-completeness constraint, not a universal default.",
    "Set filing_cadence_basis=explicit only when the user names annual or quarterly source material.",
    "Set filing_cadence_basis=evidence_structure when one cadence is needed because the requested page is normally complete only at that cadence. Full note breakdowns, governance records, segment or operating-capacity disclosures, and comparable multi-company disclosure tables are generally annual evidence. A current point-in-time balance or ratio can use any cadence unless the user specifies one.",
    "Set filing_cadence_basis=not_applicable when filing is not selected. Then filing_cadence must be any.",
    "Respect an explicit annual or quarterly request. Otherwise infer annual only from evidence structure; never infer quarterly merely because the user asks for the newest observation.",
    "Period scope and filing cadence are independent. latest+any means the newest available filing observation; latest+annual means the newest annual disclosure.",
    "industries may contain only exact values from INDUSTRY DIRECTORY.",
    "query_pack must always be null. There are no deterministic statement packs.",
    "Temporal decision table: latest means one newest snapshot; recent means several recent observations with no fixed year window; bounded means a multi-period window, a since/between range, or a named historical period; all means no time request.",
    "A plural period such as a number of years is always bounded, never latest. Resolve its inclusive from_year and to_year using today's date and whether completed periods were requested.",
    "Set temporal_mode=latest only when the user explicitly asks for one newest snapshot. Do not assume latest merely because recent evidence may be useful.",
    "Set temporal_mode=recent when the user asks for recent observations, events, filings, or announcements in the plural without giving a fixed year window.",
    "Set temporal_mode=bounded for every requested historical window and provide both years. Otherwise use all and leave both years null.",
    "For latest, recent, or bounded mode, first copy an actual date, range, named period, duration, or recency phrase from the request verbatim into time_expression. If no such exact phrase exists, temporal_mode must be all. A document action such as announced, reported, filed, published, or disclosed is not a time expression.",
    "A valid time_expression answers when. An event, transaction, measurement, evidence object, or action phrase answers what and is never a time expression even when it is quoted verbatim. When no phrase answers when, use temporal_mode=all.",
    "For all mode time_expression must be null.",
    "Before returning, audit temporal consistency: when route temporal_mode is all, every facet temporal_mode must be all; when it is latest, recent, or bounded, at least one facet must use that mode and other facets may use all.",
    "For a one-facet plan, facet temporal_mode must always equal route temporal_mode.",
    "A financial-model request with no time expression uses all; never invent a default time window.",
    "workflow is financial_model only when the user is assembling financial-model inputs; otherwise it is browse.",
    "A financial_model plan must split the requested foundation into at least three independently searchable evidence facets, normally income statement, balance sheet, and cash flow evidence.",
    `Today is ${referenceDate}.`,
    "Return JSON only with exactly this shape:",
    '{"literal_query":"cash and bank balances","evidence_needs":["cash and bank balance"],"search_facets":[{"label":"Cash balance composition","evidence_need":"cash and bank balance","lexical_query":"cash treasury bank balances composition","field_queries":{"key":"cash balance composition","summary":"cash treasury bank balances composition","text":"cash and balances with treasury banks","heading":"cash and balances with treasury banks"},"corpora":["filing"],"corpus_mode":"inferred","filing_cadence":"any","filing_cadence_basis":"evidence_structure","temporal_mode":"latest"}],"entities":[{"ticker":"HBL","mention":"HBL","confidence":1,"role":"required"}],"industries":[],"unresolved_entities":[],"from_year":null,"to_year":null,"temporal_mode":"latest","time_expression":"latest","continuation":false,"corpora":["filing"],"corpus_mode":"inferred","source_expression":null,"filing_cadence":"any","filing_cadence_basis":"evidence_structure","workflow":"browse","query_pack":null,"interpretation":"Latest cash balance disclosure for HBL"}',
    "",
    "TICKER DIRECTORY",
    directory,
    "",
    "INDUSTRY DIRECTORY",
    industryDirectory,
    "",
    "PRIOR SEARCH CONTEXT",
    priorContext ? JSON.stringify(priorContext) : "none",
  ].join("\n");

  let response: Response;
  try {
    response = await fetch("https://generativelanguage.googleapis.com/v1beta/openai/chat/completions", {
      method: "POST",
      headers: {
        Authorization: `Bearer ${apiKey}`,
        "Content-Type": "application/json",
      },
      body: JSON.stringify({
        model: routerModel,
        reasoning_effort: reasoningEffort,
        response_format: ROUTE_RESPONSE_FORMAT,
        messages: [
          { role: "system", content: system },
          { role: "user", content: query },
        ],
      }),
      cache: "no-store",
      signal: AbortSignal.timeout(timeoutMs),
    });
  } catch (error) {
    const detail = error instanceof Error ? error.message : "Router request failed.";
    console.error("Search router failed", { detail });
    throw new SearchRouterError("Search could not be planned reliably. Please retry.", 502, detail);
  }
  if (!response.ok) {
    const detail = `Router provider returned HTTP ${response.status}.`;
    console.error("Search router failed", { detail });
    throw new SearchRouterError("Search could not be planned reliably. Please retry.", 502, detail);
  }

  const payload = await response.json() as {
    choices?: Array<{ message?: { content?: string } }>;
  };
  const content = payload.choices?.[0]?.message?.content?.trim() || "";
  if (!content) {
    throw new SearchRouterError(
      "Search could not be planned reliably. Please retry.",
      502,
      "The router response did not contain JSON content.",
    );
  }
  try {
    return normalizeModelRoute(
      parseModelJson(content),
      entityDirectory,
      industryKeys,
    );
  } catch (error) {
    const detail = error instanceof Error ? error.message : "The router response was unusable.";
    console.error("Search router failed", { detail });
    throw new SearchRouterError("Search could not be planned reliably. Please retry.", 502, detail);
  }
}

function validatedPriorContext(value: PriorSearchContext | undefined): PriorSearchContext | null {
  if (!value || typeof value !== "object") return null;
  return {
    naturalQuery: compactText(value.naturalQuery, 500),
    tickers: Array.isArray(value.tickers)
      ? value.tickers.map((ticker) => compactText(ticker, 20).toUpperCase()).slice(0, 12)
      : [],
    industries: Array.isArray(value.industries)
      ? value.industries.map((industry) => compactText(industry, 120).toLowerCase()).slice(0, 4)
      : [],
    years: Array.isArray(value.years)
      ? value.years.map(Number).filter(isIntegerYear).slice(0, 10)
      : [],
    corpora: Array.isArray(value.corpora)
      ? value.corpora.filter((corpus): corpus is CorpusKind => ALLOWED_CORPORA.has(corpus))
      : [],
    filingCadence: ALLOWED_CADENCES.has(value.filingCadence as FilingCadence)
      ? value.filingCadence
      : "any",
    temporalMode: ALLOWED_TEMPORAL_MODES.has(value.temporalMode as TemporalMode)
      ? value.temporalMode
      : undefined,
    latest: value.latest === true,
    searchFacets: Array.isArray(value.searchFacets) ? value.searchFacets.slice(0, 4) : [],
  };
}

export function materializeModelRoute(
  query: string,
  modelRoute: ModelRoute,
  entities: Entity[],
  priorContext: PriorSearchContext | null,
): AppliedSearch {
  const entityMap = new Map(entities.map((entity) => [entity.ticker, entity]));
  const continuation = modelRoute.continuation && Boolean(priorContext?.naturalQuery);
  const resolved = new Map<string, RoutedEntity>();
  if (continuation) {
    for (const priorTicker of priorContext?.tickers || []) {
      const entity = entityMap.get(priorTicker);
      if (!entity) continue;
      resolved.set(priorTicker, {
        ticker: priorTicker,
        company_name: entity.company_name,
        mention: priorTicker,
        confidence: 1,
        role: "required",
      });
    }
  }
  for (const candidate of modelRoute.entities) {
    const ticker = compactText(candidate.ticker, 20).toUpperCase();
    const mention = compactText(candidate.mention, 120);
    const entity = entityMap.get(ticker);
    if (!entity) continue;
    resolved.set(ticker, {
      ticker,
      company_name: entity.company_name,
      mention,
      confidence: candidate.confidence,
      role: candidate.role,
    });
  }
  const resolvedEntities = [...resolved.values()];

  const industryMap = new Map(
    [...new Set(entities.map((entity) => compactText(entity.industry, 120)).filter(Boolean))]
      .map((industry) => [normalizedKey(industry), industry.toLowerCase()]),
  );
  const routedIndustries = modelRoute.industries
    .map((industry) => industryMap.get(normalizedKey(industry)))
    .filter((industry): industry is string => Boolean(industry));
  const industries = [...new Set([
    ...routedIndustries,
    ...(continuation ? priorContext?.industries || [] : []),
  ])].slice(0, 4);

  const bounded = modelRoute.temporal_mode === "bounded";
  const fromYear = bounded ? modelRoute.from_year : null;
  const toYear = bounded ? modelRoute.to_year : null;
  const safeFrom = fromYear && toYear ? Math.min(fromYear, toYear) : fromYear;
  const safeTo = fromYear && toYear ? Math.max(fromYear, toYear) : toYear;
  const yearCount = safeFrom && safeTo ? Math.min(10, safeTo - safeFrom + 1) : 0;
  const years = yearCount > 0
    ? Array.from({ length: yearCount }, (_, index) => safeFrom! + index)
    : [];

  const corpora: CorpusKind[] = [...new Set(modelRoute.corpora)];
  const searchFacets = modelRoute.search_facets.map((facet) => ({
    label: compactText(facet.label, 72),
    evidenceNeed: compactText(facet.evidence_need, 120),
    lexicalQuery: compactText(facet.lexical_query, 240),
    fieldQueries: {
      key: compactText(facet.field_queries.key, 240),
      summary: compactText(facet.field_queries.summary, 240),
      text: compactText(facet.field_queries.text, 240),
      heading: compactText(facet.field_queries.heading, 240),
    },
    evidenceNeeds: modelRoute.evidence_needs.map((need) => compactText(need, 120)),
    corpora: [...new Set(facet.corpora)],
    filingCadence: facet.filing_cadence,
    filingCadenceBasis: facet.filing_cadence_basis,
    temporalMode: facet.temporal_mode,
  }));
  const primaryFacet = modelRoute.search_facets[0];
  const unresolvedEntities = modelRoute.unresolved_entities
    .map((value) => compactText(value, 120))
    .filter((value) => value.length >= 2)
    .slice(0, 8);
  if (unresolvedEntities.length > 0 && resolvedEntities.length === 0) {
    throw new SearchRouterError(
      `No corpus company matched: ${unresolvedEntities.join(", ")}.`,
      422,
    );
  }

  return {
    naturalQuery: query,
    literalQuery: compactText(modelRoute.literal_query, 240),
    lexicalQuery: compactText(primaryFacet.lexical_query, 240),
    fieldQueries: {
      key: compactText(primaryFacet.field_queries.key, 240),
      summary: compactText(primaryFacet.field_queries.summary, 240),
      text: compactText(primaryFacet.field_queries.text, 240),
      heading: compactText(primaryFacet.field_queries.heading, 240),
    },
    evidenceNeeds: modelRoute.evidence_needs.map((need) => compactText(need, 120)),
    searchFacets,
    tickers: resolvedEntities.map((entity) => entity.ticker),
    requiredTickers: resolvedEntities
      .filter((entity) => entity.role === "required")
      .map((entity) => entity.ticker),
    supportingTickers: resolvedEntities
      .filter((entity) => entity.role === "supporting")
      .map((entity) => entity.ticker),
    industries,
    entities: resolvedEntities,
    unresolvedEntities,
    years,
    dateFrom: safeFrom ? `${safeFrom}-01-01` : null,
    dateTo: safeTo ? `${safeTo}-12-31` : null,
    corpora,
    filingCadence: modelRoute.filing_cadence,
    filingCadenceBasis: modelRoute.filing_cadence_basis,
    temporalMode: modelRoute.temporal_mode,
    latest: modelRoute.temporal_mode === "latest",
    preferRecent: modelRoute.temporal_mode === "recent",
    workflow: modelRoute.workflow,
    queryPack: null,
    interpretation: compactText(modelRoute.interpretation, 320),
    routedBy: "router",
  };
}

export async function POST(request: Request) {
  const auth = await requireApiUser(request);
  if (isAuthResponse(auth)) return auth;

  let query = "";
  let clientYear: number | null = null;
  let priorContext: PriorSearchContext | null = null;
  try {
    const body = await request.json() as {
      query?: string;
      clientYear?: number;
      context?: PriorSearchContext;
    };
    query = compactText(body.query, 2_000);
    const proposedClientYear = Number(body.clientYear);
    clientYear = isIntegerYear(proposedClientYear) && proposedClientYear >= 2020
      ? proposedClientYear
      : null;
    priorContext = validatedPriorContext(body.context);
  } catch {
    return Response.json({ error: "Request body must be valid JSON." }, { status: 400 });
  }
  if (!query) return Response.json({ error: "Enter a search request." }, { status: 400 });

  const directoryResponse = await corpusApiFetch("/api/entities", { cache: "no-store" });
  if (!directoryResponse.ok) {
    return Response.json({ error: "The company directory is unavailable." }, { status: 502 });
  }
  const directoryPayload = await directoryResponse.json() as { entities?: Entity[] };
  const responseDate = directoryResponse.headers.get("date");
  const responseDateValue = responseDate ? new Date(responseDate) : null;
  const responseYear = responseDateValue && !Number.isNaN(responseDateValue.getTime())
    ? responseDateValue.getUTCFullYear()
    : null;
  const referenceYear = responseYear && responseYear >= 2020 && responseYear <= 2100
    ? responseYear
    : clientYear || 2026;
  const referenceDate = responseDateValue && responseYear === referenceYear
    ? responseDateValue.toISOString().slice(0, 10)
    : `${referenceYear}-01-01`;
  const entities = (directoryPayload.entities || [])
    .filter((entity) => entity.ticker && entity.company_name);

  let modelRoute: ModelRoute;
  try {
    modelRoute = await routeWithModel(query, entities, referenceDate, priorContext);
  } catch (error) {
    const status = error instanceof SearchRouterError ? error.status : 502;
    const message = error instanceof Error ? error.message : "Search routing failed.";
    return Response.json({ error: message }, {
      status,
      headers: { "Cache-Control": "no-store" },
    });
  }

  try {
    return Response.json(
      materializeModelRoute(query, modelRoute, entities, priorContext),
      { headers: { "Cache-Control": "no-store" } },
    );
  } catch (error) {
    const status = error instanceof SearchRouterError ? error.status : 502;
    const message = error instanceof Error ? error.message : "Search routing failed.";
    return Response.json({ error: message }, {
      status,
      headers: { "Cache-Control": "no-store" },
    });
  }
}
