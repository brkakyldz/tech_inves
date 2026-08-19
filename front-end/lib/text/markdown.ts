/**
 * The report archive/preview cards show a short `excerpt` string that comes
 * straight from the API as raw markdown (it's sliced from the report body by
 * the pipeline). Card copy is plain text, so this strips the common markdown
 * syntax we actually see in excerpts -- blockquote markers, bold/italic
 * emphasis, links -- rather than rendering markdown in a one-line summary.
 */
export function stripMarkdown(input: string): string {
  return input
    .split("\n")
    .map((line) => line.replace(/^\s{0,3}>+\s?/, "")) // blockquote markers, per line
    .join(" ")
    .replace(/\[([^\]]*)\]\([^)]*\)/g, "$1") // [text](url) -> text
    .replace(/(\*\*\*|___)(.*?)\1/g, "$2") // bold+italic
    .replace(/(\*\*|__)(.*?)\1/g, "$2") // bold
    .replace(/(\*|_)(.*?)\1/g, "$2") // italic
    .replace(/`([^`]*)`/g, "$1") // inline code
    .replace(/^#{1,6}\s+/gm, "") // headings
    .replace(/\s+/g, " ")
    .trim();
}
