/** Streaming block splitter (§6.4): split accumulated text into stable blocks
 * plus one unstable tail. A block seals on a blank-line boundary or when a
 * code fence closes; an unterminated fence keeps everything from its opener in
 * the tail so partial code never half-renders. */

export interface SplitResult {
  stable: string[];
  tail: string;
}

const FENCE = /^(\s{0,3})(`{3,}|~{3,})/;

export function splitBlocks(text: string): SplitResult {
  const lines = text.split("\n");
  const stable: string[] = [];
  let current: string[] = [];
  let fenceChar: string | null = null;
  let fenceLen = 0;

  const seal = () => {
    const block = current.join("\n").trimEnd();
    if (block.trim().length > 0) stable.push(block);
    current = [];
  };

  for (let i = 0; i < lines.length; i++) {
    const line = lines[i]!;
    const isLast = i === lines.length - 1;
    const fenceMatch = FENCE.exec(line);

    if (fenceChar === null && fenceMatch) {
      fenceChar = fenceMatch[2]![0]!;
      fenceLen = fenceMatch[2]!.length;
      current.push(line);
      continue;
    }
    if (fenceChar !== null) {
      current.push(line);
      const closes =
        fenceMatch &&
        fenceMatch[2]![0] === fenceChar &&
        fenceMatch[2]!.length >= fenceLen &&
        line.trim() === fenceMatch[2];
      if (closes) {
        fenceChar = null;
        if (!isLast) seal(); // fence-close seals (last line stays tail-adjacent)
      }
      continue;
    }
    if (line.trim() === "") {
      seal();
      continue;
    }
    current.push(line);
  }

  // whatever is still open — including an unterminated fence — is the tail
  return { stable, tail: current.join("\n") };
}

/** djb2 — cheap content hash for stable-block memo keys. */
export function contentHash(value: string): string {
  let hash = 5381;
  for (let i = 0; i < value.length; i++) {
    hash = ((hash << 5) + hash + value.charCodeAt(i)) | 0;
  }
  return (hash >>> 0).toString(36) + ":" + value.length.toString(36);
}
