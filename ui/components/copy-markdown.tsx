"use client";

import { useState } from "react";

export function CopyMarkdown({ value }: { value: string }) {
  const [copied, setCopied] = useState(false);

  async function copy() {
    await navigator.clipboard.writeText(value);
    setCopied(true);
    window.setTimeout(() => setCopied(false), 1500);
  }

  return (
    <button className="button" onClick={copy} type="button">
      {copied ? "Copied" : "Copy Markdown"}
    </button>
  );
}
