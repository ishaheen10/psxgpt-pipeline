import tickers from "../../../tickers100.json" with { type: "json" };

const input = await new Promise((resolve, reject) => {
  let raw = "";
  process.stdin.setEncoding("utf8");
  process.stdin.on("data", (chunk) => { raw += chunk; });
  process.stdin.on("end", () => resolve(raw));
  process.stdin.on("error", reject);
});

const payload = JSON.parse(input);
const rows = (payload[0]?.results || [])
  .filter((row) => String(row.query || "").trim())
  .sort((left, right) => String(left.created_at).localeCompare(String(right.created_at)));
const tickerSet = new Set(tickers.map((row) => String(row.Symbol || "").toUpperCase()).filter(Boolean));
const priorBySession = new Map();

const SHAPES = [
  {
    id: "hybrid_numeric_narrative",
    label: "Numeric analysis plus filing explanation",
    test: (q) =>
      /\b(compare|trend|growth|margin|ratio|profit|revenue|deposits?|advances?|cash flow|cfo|roe|roa|eps|valuation|capacity)\b/i.test(q)
      && /\b(explain|commentary|discussion|drivers?|guidance|outlook|strategy|risks?|caveats?|management|directors? report|filings?)\b/i.test(q),
  },
  {
    id: "exact_note_or_line_item",
    label: "Exact note, disclosure, or line item",
    test: (q) =>
      /\b(break-?up|breakdown|segregation|exposure|holding|marketing|advertisement|publicity|receivables?|borrowings?|cost of goods|deposit mix|loan mix|segment(?:al)?|note(?:s)?\b|statement of|profit and loss|balance sheet|cash flow statement)\b/i.test(q),
  },
  {
    id: "screen_rank_compare",
    label: "Screen, rank, or peer comparison",
    test: (q) =>
      /\b(top \d+|highest|lowest|rank|screen|shortlist|which (?:companies|stocks|banks)|compare\b|sector median|across all|all psx)\b/i.test(q),
  },
  {
    id: "model_valuation_scenario",
    label: "Model, valuation, or scenario",
    test: (q) =>
      /\b(financial model|dcf|discounted cash flow|intrinsic value|fair value|fair price|target price|scenario|projection|forecast|margin of safety|sensitivity|assum(?:e|ing|ption)|multiple)\b/i.test(q),
  },
  {
    id: "filing_narrative",
    label: "Narrative, strategy, risk, or outlook",
    test: (q) =>
      /\b(explain|why|commentary|discussion|drivers?|guidance|outlook|strategy|risks?|headwinds?|catalysts?|management|directors? report|forward[- ]looking|capacity utilization|expansion|working capital)\b/i.test(q),
  },
  {
    id: "metric_history",
    label: "Metric history or calculation",
    test: (q) =>
      /\b(trend|growth|margin|ratio|profit|revenue|deposits?|advances?|assets?|equity|cash flow|cfo|roe|roa|eps|pe\b|price to book|market cap|compensation|capacity|offtake|quarter|annual periods?|years?)\b/i.test(q),
  },
  {
    id: "announcement_event",
    label: "Announcement, corporate action, or event",
    test: (q) =>
      /\b(announcement|notice|acquisition|merger|dividend|stock split|rights? issue|bonus issue|ipo|contract|order book|material information|result announcement)\b/i.test(q),
  },
  {
    id: "macro_sector",
    label: "Macro or sector research",
    test: (q) =>
      /\b(pakistan|macro|macroeconomic|economic survey|inflation|interest rate|policy rate|fuel shock|imports?|exports?|sector|industry|tariff|circular debt|gas|feedstock)\b/i.test(q),
  },
  {
    id: "source_navigation",
    label: "Source-page or citation navigation",
    test: (q) =>
      /\b(source pages?|page links?|exact (?:filing )?pages?|cite|citation|mark sources?|latest annual report pages?|retrieve .* report pages?)\b/i.test(q),
  },
  {
    id: "investment_decision",
    label: "Investment recommendation or portfolio decision",
    test: (q) =>
      /\b(should i buy|good stock|stocks? to buy|undervalued|overvalued|entry|exit|sell|hold|portfolio|invest|capital appreciation|return within|conviction)\b/i.test(q),
  },
  {
    id: "company_overview",
    label: "Company overview or deep research",
    test: (q) =>
      /\b(tell me about|overview|deep research|research report|fundamentals?|analyse|analyze|how is .+ doing|business of)\b/i.test(q),
  },
  {
    id: "capability_or_greeting",
    label: "Capability, coverage, or greeting",
    test: (q) =>
      /^(hi|hello|hey|thanks?|ok)\b/i.test(q)
      || /\b(what do you do|what tickers|in your database|do you have|data available|right tool)\b/i.test(q),
  },
];

const shapeStats = new Map(SHAPES.map((shape) => [shape.id, {
  id: shape.id,
  label: shape.label,
  rows: 0,
  distinct: new Set(),
  sessions: new Set(),
  samples: [],
}]));
const primaryCounts = new Map();
const entityCounts = new Map();
const wordCounts = [];
let followups = 0;
let multiIntent = 0;
let unmatched = 0;

for (const row of rows) {
  const query = String(row.query).replace(/\s+/g, " ").trim();
  const lower = query.toLowerCase();
  const words = query.match(/\S+/g) || [];
  wordCounts.push(words.length);
  const session = String(row.session_id || "");
  const isFollowup = Boolean(session && priorBySession.has(session));
  if (isFollowup) followups += 1;
  if (session) priorBySession.set(session, query);

  const mentioned = [...new Set(
    (query.toUpperCase().match(/\b[A-Z][A-Z0-9.-]{1,15}\b/g) || [])
      .filter((token) => tickerSet.has(token)),
  )];
  const bucket = mentioned.length === 0 ? "0" : mentioned.length === 1 ? "1" : mentioned.length <= 4 ? "2-4" : "5+";
  entityCounts.set(bucket, (entityCounts.get(bucket) || 0) + 1);

  const matches = SHAPES.filter((shape) => shape.test(query));
  if (matches.length >= 2) multiIntent += 1;
  if (!matches.length) unmatched += 1;
  const primary = matches[0]?.id || (isFollowup && words.length <= 18 ? "conversational_followup" : "other");
  primaryCounts.set(primary, (primaryCounts.get(primary) || 0) + 1);

  for (const shape of matches) {
    const stat = shapeStats.get(shape.id);
    stat.rows += 1;
    stat.distinct.add(lower);
    if (session) stat.sessions.add(session);
    if (
      stat.samples.length < 8
      && query.length >= 18
      && query.length <= 320
      && !stat.samples.some((sample) => sample.toLowerCase() === lower)
    ) {
      stat.samples.push(query);
    }
  }
}

wordCounts.sort((left, right) => left - right);
const percentile = (fraction) => wordCounts[Math.min(wordCounts.length - 1, Math.floor(wordCounts.length * fraction))] || 0;

const report = {
  corpus: {
    rows: rows.length,
    distinct_queries: new Set(rows.map((row) => String(row.query).replace(/\s+/g, " ").trim().toLowerCase())).size,
    sessions: new Set(rows.map((row) => row.session_id).filter(Boolean)).size,
    date_from: rows[0]?.created_at || null,
    date_to: rows.at(-1)?.created_at || null,
  },
  query_length_words: {
    median: percentile(0.5),
    p75: percentile(0.75),
    p90: percentile(0.9),
    p95: percentile(0.95),
  },
  conversational_followups: followups,
  multi_intent_rows: multiIntent,
  unmatched_rows: unmatched,
  ticker_mentions: Object.fromEntries([...entityCounts.entries()].sort()),
  primary_shape_counts: Object.fromEntries([...primaryCounts.entries()].sort((left, right) => right[1] - left[1])),
  overlapping_shapes: [...shapeStats.values()]
    .map((stat) => ({
      id: stat.id,
      label: stat.label,
      rows: stat.rows,
      distinct_queries: stat.distinct.size,
      sessions: stat.sessions.size,
      samples: stat.samples,
    }))
    .sort((left, right) => right.rows - left.rows),
};

process.stdout.write(`${JSON.stringify(report, null, 2)}\n`);
