export function normalizedKey(value: unknown): string {
  let output = "";
  let pendingSpace = false;
  for (const character of String(value ?? "").normalize("NFKC").toLowerCase()) {
    const code = character.charCodeAt(0);
    const alphaNumeric = (code >= 97 && code <= 122) || (code >= 48 && code <= 57);
    if (alphaNumeric) {
      if (pendingSpace && output) output += " ";
      output += character;
      pendingSpace = false;
    } else if (output) {
      pendingSpace = true;
    }
  }
  return output.trim();
}

export function textContainsExactSpan(text: string, span: string): boolean {
  const textKey = normalizedKey(text);
  const spanKey = normalizedKey(span);
  return Boolean(spanKey) && ` ${textKey} `.includes(` ${spanKey} `);
}
