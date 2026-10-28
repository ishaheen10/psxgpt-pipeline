export type IndicatorStatus = "connected" | "partial" | "gap";
export type SignalTone = "positive" | "negative" | "mixed" | "unknown";

export type Indicator = {
  id: string;
  name: string;
  publisher: string;
  cadence: string;
  timing: "leading" | "coincident" | "lagging";
  status: IndicatorStatus;
  why: string;
  query: string;
  current?: string;
  pageIds: string[];
};

export type Driver = {
  id: string;
  name: string;
  shortName: string;
  question: string;
  mechanism: string;
  currentRead: string;
  tone: SignalTone;
  sectionId: string;
  pageIds: string[];
  indicators: Indicator[];
  companyTransmission: Array<{ ticker: string; implication: string }>;
};

export const drivers: Driver[] = [
  {
    id: "farmer-economics",
    name: "Farmer economics",
    shortName: "Farmer economics",
    question: "Can farmers afford to apply fertilizer at current crop and input prices?",
    mechanism: "Crop realization, support policy, water and credit shape planted acreage and fertilizer intensity before company revenue appears.",
    currentRead: "Phosphate affordability is visibly weaker, but crop-price, water and credit evidence are not yet connected into this pack.",
    tone: "mixed",
    sectionId: "demand_affordability",
    pageIds: ["P001", "P006", "P024", "P025", "P026", "P027"],
    indicators: [
      { id: "exchange-ratio", name: "Crop-to-fertilizer exchange ratio", publisher: "NFDC", cadence: "annual", timing: "leading", status: "connected", why: "Measures how many units of crop output are needed to buy fertilizer.", query: "crop fertilizer exchange ratio wheat cotton rice", current: "Ratios worsened materially in 2024–25", pageIds: ["P024", "P025", "P026", "P027"] },
      { id: "fertilizer-retail", name: "Domestic fertilizer prices", publisher: "NFDC / PBS", cadence: "monthly", timing: "leading", status: "connected", why: "Price changes can suppress application rates before they appear in quarterly sales.", query: "fertilizer retail prices urea DAP farmer", current: "DAP was 18.7% higher YoY in April", pageIds: ["P006"] },
      { id: "crop-prices", name: "Crop prices and support policy", publisher: "PBS / MNFSR", cadence: "weekly, monthly and event", timing: "leading", status: "gap", why: "Farmer revenue expectations determine willingness to spend on nutrients.", query: "wheat cotton rice sugarcane prices support price Pakistan", pageIds: [] },
      { id: "water", name: "Water availability and sowing conditions", publisher: "IRSA / FCA", cadence: "10-daily and seasonal", timing: "leading", status: "gap", why: "Water availability changes acreage and application timing.", query: "IRSA water availability Kharif Rabi crop sowing", pageIds: [] },
      { id: "agri-credit", name: "Agricultural credit", publisher: "State Bank of Pakistan", cadence: "monthly", timing: "leading", status: "gap", why: "Credit availability affects input purchasing capacity.", query: "agricultural credit disbursement fertilizer input Pakistan", pageIds: [] },
    ],
    companyTransmission: [
      { ticker: "FFC", implication: "Greater urea and DAP volume sensitivity, partly cushioned by distribution breadth." },
      { ticker: "EFERT", implication: "Volume discipline becomes more important when farmer affordability weakens." },
      { ticker: "FATIMA", implication: "Multi-product exposure makes crop and regional mix especially relevant." },
      { ticker: "AGL", implication: "Lower operating resilience magnifies demand and working-capital shocks." },
    ],
  },
  {
    id: "market-balance",
    name: "Demand and inventory",
    shortName: "Demand & inventory",
    question: "Is industry inventory tightening or building—and in which product?",
    mechanism: "Opening stock plus production and imports, less offtake and exports, determines pricing pressure and plant utilization.",
    currentRead: "Urea is comfortably supplied; DAP becomes tight if planned imports do not arrive on time.",
    tone: "mixed",
    sectionId: "market_balance",
    pageIds: ["P001", "P003", "P004", "P005", "P008", "P009", "P010", "P011", "P012", "P013", "P014", "P015", "P016", "P017", "P018"],
    indicators: [
      { id: "monthly-offtake", name: "Product and province offtake", publisher: "NFDC", cadence: "monthly", timing: "coincident", status: "connected", why: "The cleanest direct read on sector demand.", query: "urea DAP offtake province monthly", current: "April urea offtake +84.9% YoY", pageIds: ["P001", "P003", "P008", "P009", "P010"] },
      { id: "closing-stock", name: "Industry and company inventory", publisher: "NFDC", cadence: "monthly", timing: "coincident", status: "connected", why: "Reveals pricing pressure and future production discipline.", query: "urea DAP closing inventory company stock", current: "805kt urea and 203kt DAP at April close", pageIds: ["P004", "P011", "P012", "P013"] },
      { id: "supply-outlook", name: "Seasonal supply-demand outlook", publisher: "NFDC", cadence: "monthly / seasonal", timing: "leading", status: "connected", why: "Turns the latest balance into an explicit forward inventory path.", query: "Kharif fertilizer supply demand outlook imports", current: "September DAP stock projected at 17kt", pageIds: ["P005", "P014", "P015"] },
      { id: "issue-lag", name: "Latest monthly review", publisher: "NFDC", cadence: "monthly", timing: "coincident", status: "partial", why: "The primary series is valuable only if the latest issue is available.", query: "NFDC fertilizer review May June July 2026", current: "Captured through April; later issues are missing", pageIds: [] },
    ],
    companyTransmission: [
      { ticker: "FFC", implication: "Share gains and DAP inventory make both volume and price realization material." },
      { ticker: "EFERT", implication: "Large disclosed urea inventory makes conversion speed a central test." },
      { ticker: "FATIMA", implication: "Plant availability and product mix complicate a simple national-volume read." },
      { ticker: "AGL", implication: "Production interruptions can overwhelm an otherwise favorable market balance." },
    ],
  },
  {
    id: "input-economics",
    name: "Gas and input economics",
    shortName: "Gas economics",
    question: "Who has the structural gas-cost and availability advantage?",
    mechanism: "Feed gas determines urea economics; fuel gas and RLNG affect utilization, cash cost and the ability to compete on price.",
    currentRead: "The official evidence confirms major price dispersion, but current plant-level allocations and realized costs remain incomplete.",
    tone: "negative",
    sectionId: "supply_gas_capacity",
    pageIds: ["P028", "P037", "P038", "P039", "P040", "P041", "P042", "P043", "P065", "P068", "P070", "P071", "P083", "P091"],
    indicators: [
      { id: "feed-gas", name: "Feed and fuel gas tariffs", publisher: "OGRA / Petroleum Division", cadence: "semiannual and event", timing: "leading", status: "partial", why: "Directly shifts plant cash cost and relative pricing power.", query: "fertilizer feed gas fuel gas consumer tariff", current: "SNGPL feed gas baseline Rs 1,597/MMBTU", pageIds: ["P028"] },
      { id: "rlng", name: "RLNG notified price", publisher: "OGRA", cadence: "monthly", timing: "leading", status: "connected", why: "Sets the exposure of RLNG-dependent production.", query: "RLNG notified sale price fertilizer SNGPL", current: "July SNGPL transmission US$23.70/MMBTU", pageIds: ["P037", "P038", "P039"] },
      { id: "allocation", name: "Plant-level gas allocation", publisher: "ECC / Petroleum Division", cadence: "event", timing: "leading", status: "gap", why: "Availability can matter more than tariff for marginal plants.", query: "fertilizer gas allocation plant ECC Mari SNGPL", pageIds: ["P040", "P041", "P042", "P043"] },
      { id: "outages", name: "Plant outage and resumption", publisher: "PSX company announcements", cadence: "event", timing: "coincident", status: "connected", why: "Converts policy and gas constraints into actual lost production.", query: "fertilizer plant outage gas resumption", current: "Recent EFERT, FATIMA and AGL disruptions captured", pageIds: ["P070", "P071", "P083", "P091"] },
    ],
    companyTransmission: [
      { ticker: "FFC", implication: "Lower-cost gas access supports share and pricing flexibility." },
      { ticker: "EFERT", implication: "Management explicitly links pricing discipline and lost share to higher gas cost." },
      { ticker: "FATIMA", implication: "Sheikhupura restart timing is a direct earnings variable." },
      { ticker: "AGL", implication: "RLNG curtailment has already translated into force majeure and losses." },
    ],
  },
  {
    id: "imports-pricing",
    name: "Imports and pricing",
    shortName: "Imports & pricing",
    question: "Will import parity and international prices tighten domestic product economics?",
    mechanism: "International prices, freight, duties and import timing set DAP availability and the price umbrella for domestic products.",
    currentRead: "Domestic and international phosphate prices rose together; the margin of importers is not directly evidenced.",
    tone: "negative",
    sectionId: "price_margin_transmission",
    pageIds: ["P001", "P005", "P006", "P007", "P033", "P034", "P035", "P036", "P056"],
    indicators: [
      { id: "international", name: "International urea and DAP prices", publisher: "NFDC", cadence: "monthly", timing: "leading", status: "connected", why: "Defines import parity before imported product reaches the domestic market.", query: "international urea DAP prices Middle East Morocco", current: "Both urea and DAP rose through April", pageIds: ["P007"] },
      { id: "imports", name: "Fertilizer imports by product", publisher: "NFDC / PBS", cadence: "monthly", timing: "leading", status: "connected", why: "Import timing determines whether projected tightness becomes physical scarcity.", query: "fertilizer imports urea DAP quantity Pakistan", current: "Only 55kt DAP imports scheduled for June–July", pageIds: ["P005", "P033", "P034", "P035", "P036"] },
      { id: "customs", name: "Customs duties and exemptions", publisher: "FBR", cadence: "event", timing: "leading", status: "gap", why: "Duties and exemptions change landed cost immediately.", query: "active customs SRO urea DAP fertilizer phosphate", pageIds: [] },
      { id: "domestic-price", name: "Domestic product prices", publisher: "NFDC / PBS", cadence: "monthly", timing: "coincident", status: "connected", why: "Shows whether international cost pressure is passing through.", query: "domestic urea DAP fertilizer prices", current: "All major products rose MoM in April", pageIds: ["P006"] },
    ],
    companyTransmission: [
      { ticker: "FFC", implication: "DAP inventory creates upside if scarcity develops, but also ties up capital." },
      { ticker: "EFERT", implication: "Phosphate volumes and margin depend on landed cost and farmer affordability." },
      { ticker: "FATIMA", implication: "Product diversity changes exposure to phosphate import economics." },
      { ticker: "AGL", implication: "Predominantly urea exposure makes domestic gas economics more material than DAP parity." },
    ],
  },
  {
    id: "policy",
    name: "Policy and regulation",
    shortName: "Policy",
    question: "Did allocation, subsidy, tax, import or competition policy change sector economics?",
    mechanism: "Government decisions can reallocate gas, authorize imports, alter subsidy and duties, or change competitive conduct overnight.",
    currentRead: "Gas subsidy and standing allocation policy are captured; current ECC event routing and active customs instruments remain incomplete.",
    tone: "unknown",
    sectionId: "thesis_monitoring",
    pageIds: ["P028", "P040", "P041", "P042", "P043", "P097", "P098", "P099", "P100"],
    indicators: [
      { id: "ecc", name: "ECC fertilizer decisions", publisher: "Finance Division", cadence: "event", timing: "leading", status: "partial", why: "Can directly change imports, strategic stock, subsidy and gas allocation.", query: "ECC fertilizer urea DAP subsidy import gas allocation", pageIds: [] },
      { id: "gas-policy", name: "Gas allocation and pricing framework", publisher: "Petroleum Division / OGRA", cadence: "standing and event", timing: "leading", status: "partial", why: "Defines entitlement and price treatment by consumer category.", query: "fertilizer gas allocation management policy GIDC", current: "Standing 2005 allocation baseline retained", pageIds: ["P028", "P040", "P041", "P042", "P043"] },
      { id: "tax", name: "Tax and customs instruments", publisher: "FBR", cadence: "event", timing: "leading", status: "gap", why: "Changes import parity and effective product cost.", query: "fertilizer customs duty GST SRO DAP urea", pageIds: [] },
      { id: "competition", name: "Competition and merger decisions", publisher: "CCP", cadence: "event", timing: "lagging", status: "partial", why: "Matters for market structure, conduct and consolidation.", query: "CCP fertilizer urea DAP merger order", pageIds: ["P097", "P098", "P099", "P100"] },
    ],
    companyTransmission: [
      { ticker: "FFC", implication: "Capital deployment, consolidation and fertilizer policy can alter the sum-of-parts view." },
      { ticker: "EFERT", implication: "Gas pricing and allocation decisions are central to competitive positioning." },
      { ticker: "FATIMA", implication: "Policy affects plant gas availability and product economics." },
      { ticker: "AGL", implication: "Allocation and restructuring decisions can dominate conventional operating analysis." },
    ],
  },
  {
    id: "financial-response",
    name: "Financial response",
    shortName: "Financial response",
    question: "How are external changes reaching volume, margin, inventory, cash flow and capital allocation?",
    mechanism: "Company filings reveal the lagged financial expression of sector drivers and management responses.",
    currentRead: "FFC is gaining share, EFERT is protecting margin through volume discipline, while FATIMA and AGL face operational disruption.",
    tone: "mixed",
    sectionId: "company_comparison",
    pageIds: Array.from({ length: 57 }, (_, index) => `P${String(index + 44).padStart(3, "0")}`),
    indicators: [
      { id: "quarterly", name: "Quarterly results and commentary", publisher: "PSX / issuers", cadence: "quarterly", timing: "lagging", status: "connected", why: "Shows the financial result of preceding demand, cost and policy changes.", query: "fertilizer quarterly results revenue gross profit inventory cash flow", current: "FFC and EFERT through H1; FATIMA and AGL through Q1", pageIds: ["P044", "P045", "P046", "P047", "P048", "P049", "P050", "P051", "P058", "P059", "P060", "P061", "P062", "P082", "P083", "P084", "P085", "P090", "P091", "P092", "P093", "P094", "P095", "P096"] },
      { id: "briefings", name: "Management briefings", publisher: "Issuers", cadence: "quarterly / event", timing: "lagging", status: "connected", why: "Explains management interpretation, forecasts and operating choices.", query: "fertilizer corporate briefing market share inventory capex outlook", pageIds: ["P052", "P053", "P054", "P055", "P056", "P057", "P063", "P064", "P065", "P066", "P067", "P068", "P069"] },
      { id: "annual-core", name: "Optimal annual-report pages", publisher: "Issuers", cadence: "annual", timing: "lagging", status: "partial", why: "A compact selection removes narrative bulk while preserving statements, operating history and material notes.", query: "annual report six year analysis segment capex borrowings related party", current: "Only FFC Agritech notes are included in the current pack", pageIds: ["P097", "P098", "P099", "P100"] },
      { id: "events", name: "Material company events", publisher: "PSX announcements", cadence: "daily / event", timing: "leading", status: "connected", why: "Plant events and leadership changes arrive before the next financial statement.", query: "fertilizer material information plant outage resumption CEO", pageIds: ["P070", "P071", "P072"] },
    ],
    companyTransmission: [
      { ticker: "FFC", implication: "Separate operating progress from investment income, leverage and capital allocation." },
      { ticker: "EFERT", implication: "Test whether margin defense can persist without volume and cash-flow deterioration." },
      { ticker: "FATIMA", implication: "Measure the duration and financial cost of plant disruption." },
      { ticker: "AGL", implication: "Solvency and preference-share terms remain as important as operating recovery." },
    ],
  },
];

export const contextRecipes = [
  { id: "sector-brief", name: "Current sector brief", description: "Balance, affordability, gas, imports and the latest company read.", pageIds: ["P001", "P003", "P004", "P005", "P006", "P007", "P026", "P028", "P039", "P040", "P047", "P052", "P061", "P065", "P083", "P091"], output: "Decision memo" },
  { id: "leading-indicators", name: "Leading-indicator pack", description: "The evidence that can change the view before the next quarterly statements.", pageIds: ["P005", "P006", "P007", "P024", "P025", "P026", "P027", "P028", "P037", "P038", "P039", "P040", "P041", "P042", "P043", "P070", "P071"], output: "Thesis monitor" },
  { id: "company-compare", name: "Company comparison", description: "Latest result pages, briefings, selected notes and material events across four issuers.", pageIds: ["P047", "P048", "P052", "P054", "P055", "P056", "P061", "P062", "P065", "P068", "P070", "P071", "P073", "P079", "P082", "P083", "P084", "P085", "P090", "P091", "P093", "P094"], output: "Comparison table" },
  { id: "annual-core", name: "Annual-report core", description: "The target subset for six-year financials, operating history and material notes. Current evidence is incomplete.", pageIds: ["P097", "P098", "P099", "P100"], output: "Six-year model input" },
];

export const monitoringStreams = [
  { name: "NFDC Monthly Fertilizer Review", cadence: "Monthly", lastCaptured: "April 2026", expectation: "About one month after reference period", status: "late", affects: ["Farmer economics", "Demand & inventory", "Imports & pricing"] },
  { name: "OGRA RLNG notified price", cadence: "Monthly", lastCaptured: "July 2026", expectation: "Same day when posted", status: "current", affects: ["Gas economics"] },
  { name: "ECC fertilizer decisions", cadence: "Event-driven", lastCaptured: "Archive captured; routing incomplete", expectation: "Same day when posted", status: "partial", affects: ["Gas economics", "Policy"] },
  { name: "PBS fertilizer trade and prices", cadence: "Weekly / monthly", lastCaptured: "Collection available; product mapping pending", expectation: "Days to following month", status: "partial", affects: ["Farmer economics", "Imports & pricing"] },
  { name: "Issuer reports and announcements", cadence: "Daily check / quarterly reports", lastCaptured: "FFC & EFERT H1; FATIMA & AGL Q1", expectation: "Event-driven", status: "current", affects: ["Financial response"] },
  { name: "FBR customs SROs", cadence: "Event-driven", lastCaptured: "Official collection known; fertilizer filter pending", expectation: "Same day when posted", status: "gap", affects: ["Imports & pricing", "Policy"] },
];
