import { describe, expect, it } from "vitest";
import { contentHash, splitBlocks } from "./blocks";

describe("splitBlocks (§6.4 streaming splitter)", () => {
  it("keeps a growing paragraph in the tail", () => {
    const { stable, tail } = splitBlocks("Hello wor");
    expect(stable).toEqual([]);
    expect(tail).toBe("Hello wor");
  });

  it("seals a block on a blank-line boundary", () => {
    const { stable, tail } = splitBlocks("First paragraph.\n\nSecond para");
    expect(stable).toEqual(["First paragraph."]);
    expect(tail).toBe("Second para");
  });

  it("keeps an unterminated fence entirely in the tail", () => {
    const text = "Intro.\n\n```python\nprint('hi')\nprint('mo";
    const { stable, tail } = splitBlocks(text);
    expect(stable).toEqual(["Intro."]);
    expect(tail).toBe("```python\nprint('hi')\nprint('mo");
  });

  it("blank lines inside a fence do not split it", () => {
    const text = "```js\nconst a = 1;\n\nconst b = 2;\n```\n\nAfter";
    const { stable, tail } = splitBlocks(text);
    expect(stable).toEqual(["```js\nconst a = 1;\n\nconst b = 2;\n```"]);
    expect(tail).toBe("After");
  });

  it("a closed fence seals even without a trailing blank line", () => {
    const text = "```js\nlet x;\n```\nmore prose";
    const { stable, tail } = splitBlocks(text);
    expect(stable).toEqual(["```js\nlet x;\n```"]);
    expect(tail).toBe("more prose");
  });

  it("longer fences can contain shorter ones (pathological stream)", () => {
    const text = "````md\n```js\ninner\n```\n````\n\ntail";
    const { stable, tail } = splitBlocks(text);
    expect(stable).toEqual(["````md\n```js\ninner\n```\n````"]);
    expect(tail).toBe("tail");
  });

  it("multiple sealed blocks accumulate in order", () => {
    const text = "one\n\ntwo\n\nthree\n\nfour…";
    const { stable, tail } = splitBlocks(text);
    expect(stable).toEqual(["one", "two", "three"]);
    expect(tail).toBe("four…");
  });

  it("stable prefix never changes as the tail grows (memo invariant)", () => {
    const full = "alpha\n\nbeta\n\n```py\ncode()\n```\n\ngamma is growing";
    let previousStable: string[] = [];
    for (let i = 1; i <= full.length; i++) {
      const { stable } = splitBlocks(full.slice(0, i));
      // previously-sealed blocks must be a prefix of the new stable list
      expect(stable.slice(0, previousStable.length)).toEqual(previousStable);
      previousStable = stable;
    }
  });

  it("handles emoji-split chunks and RTL text", () => {
    const text = "מַה נִּשְׁתַּנָּה 🎉\n\nnext";
    const { stable, tail } = splitBlocks(text);
    expect(stable).toEqual(["מַה נִּשְׁתַּנָּה 🎉"]);
    expect(tail).toBe("next");
  });
});

describe("contentHash", () => {
  it("is stable and content-sensitive", () => {
    expect(contentHash("abc")).toBe(contentHash("abc"));
    expect(contentHash("abc")).not.toBe(contentHash("abd"));
    expect(contentHash("")).toBeTruthy();
  });
});
