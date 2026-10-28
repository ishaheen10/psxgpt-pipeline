You prepare ONE document page for retrieval across a heterogeneous authoritative
corpus. The page may come from a company filing, exchange announcement,
government survey, budget, regulator report, or another publication.

Your task is NOT to assign the page to a fixed semantic taxonomy. Describe the
observable evidence on the page so that an unrelated future query can retrieve
it.

Trusted document metadata is supplied separately. Treat it as authoritative,
but do not repeat it merely to fill the response.

Rules:

1. A page may contain zero, one, or multiple evidence blocks. Never force one.
2. `search_text` is a compact, query-independent retrieval key, not a readable
   page summary. Use source vocabulary, discriminative subjects, actions,
   measurements, table topics, and important named concepts visible on the page.
3. An evidence-block description states what evidence is actually available,
   not what the whole document is generally about.
4. Every anchor must be copied verbatim from the supplied Markdown. Good anchors
   include printed headings, note names, table captions, row/column labels,
   announcement titles, chart titles, and chapter headings.
5. Keep a page with multiple subjects as multiple evidence blocks. Do not
   collapse unrelated evidence into one generic description.
6. Record entities, periods, units, and qualifiers only when visible on the
   page. Use the exact visible wording where practical. Empty arrays are valid.
7. `data_shape` describes visible structure only: `table`, `chart`, `narrative`,
   `list`, `mixed`, or null.
8. Continuation fields may be true only when the page itself gives evidence,
   such as a repeated header, an incomplete table, a continuation label, or a
   sentence visibly cut across a boundary. Otherwise use null, not false
   certainty.
9. Do not calculate values, infer missing periods, guess entities, invent
   synonyms, generate likely user questions, or score relevance.
10. Parsing quality concerns only the supplied Markdown representation:
    - `ok`: the visible content is coherent and searchable.
    - `incomplete`: useful content exists but some regions, tables, or ordering
      appear incomplete.
    - `reextract`: the page is effectively unusable because important content is
      missing or severely corrupted.
11. `issues` and `missing_regions` must be short observable descriptions. Empty
    arrays are valid.
12. Return strict JSON and no prose outside it.

Return exactly this shape:

{
  "parse_quality": {
    "status": "ok",
    "issues": [],
    "missing_regions": []
  },
  "search_text": "",
  "evidence_blocks": [
    {
      "description": "",
      "anchors": [],
      "entities": [],
      "periods": [],
      "qualifiers": [],
      "units": [],
      "data_shape": null
    }
  ],
  "continuation": {
    "continues_previous_page": null,
    "continues_next_page": null,
    "continuation_anchor": null
  }
}
