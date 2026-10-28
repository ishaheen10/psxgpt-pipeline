import type { ReactNode } from "react";
import { type Inline, isNumericCell, parseBlocks, parseInline } from "./markdown-parse";

/**
 * Renders parsed filing markdown as React elements.
 *
 * Elements, never HTML strings: page content is OCR output from third-party
 * PDFs, so it must not be able to inject markup. All parsing lives in
 * `markdown-parse.ts`, which is plain data and unit tested.
 */

function inlineNodes(parts: Inline[], keyPrefix: string): ReactNode[] {
  return parts.map((part, index) => {
    const key = `${keyPrefix}-${index}`;
    if (part.kind === "strong") return <strong key={key}>{part.value}</strong>;
    if (part.kind === "em") return <em key={key}>{part.value}</em>;
    if (part.kind === "code") return <code key={key}>{part.value}</code>;
    return <span key={key}>{part.value}</span>;
  });
}

function text(value: string, keyPrefix: string): ReactNode[] {
  return inlineNodes(parseInline(value), keyPrefix);
}

export function MarkdownPage({ text: markdown }: { text: string }) {
  if (!markdown.trim()) {
    return <p className="md-empty">This page has no extracted text.</p>;
  }

  const blocks = parseBlocks(markdown);

  return (
    <div className="md">
      {blocks.map((block, index) => {
        const key = `b${index}`;
        switch (block.kind) {
          case "heading": {
            const Tag = `h${block.level}` as "h1";
            return <Tag key={key}>{text(block.text, key)}</Tag>;
          }
          case "rule":
            return <hr key={key} />;
          case "quote":
            return <blockquote key={key}>{text(block.text, key)}</blockquote>;
          case "list": {
            const ListTag = block.ordered ? "ol" : "ul";
            return (
              <ListTag key={key}>
                {block.items.map((item, itemIndex) => (
                  <li key={itemIndex}>{text(item, `${key}-${itemIndex}`)}</li>
                ))}
              </ListTag>
            );
          }
          case "table": {
            const headerIsEmpty = block.header.every((cell) => !cell);
            return (
              <div className="md-table-wrap" key={key}>
                <table>
                  {!headerIsEmpty && (
                    <thead>
                      <tr>
                        {Array.from({ length: block.width }, (_, column) => (
                          <th key={column}>
                            {text(block.header[column] || "", `${key}-h${column}`)}
                          </th>
                        ))}
                      </tr>
                    </thead>
                  )}
                  <tbody>
                    {block.rows.map((row, rowIndex) => (
                      <tr key={rowIndex}>
                        {Array.from({ length: block.width }, (_, column) => {
                          const value = row[column] || "";
                          return (
                            <td key={column} className={isNumericCell(value) ? "num" : undefined}>
                              {text(value, `${key}-${rowIndex}-${column}`)}
                            </td>
                          );
                        })}
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            );
          }
          default:
            return <p key={key}>{text(block.text, key)}</p>;
        }
      })}
    </div>
  );
}
