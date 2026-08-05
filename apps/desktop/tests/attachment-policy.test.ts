import { describe, expect, it } from "vitest";

import {
  ATTACHMENT_ACCEPT,
  attachmentPreflightError,
  MAX_ATTACHMENT_BYTES,
} from "../app/src/attachment-policy";

describe("attachment policy", () => {
  it("accepts the committed Office, PDF, image, and text formats", () => {
    for (const name of ["a.docx", "a.xlsx", "a.csv", "a.pdf", "a.pptx", "a.png", "a.jpg", "a.jpeg", "a.webp", "a.txt"]) {
      expect(attachmentPreflightError({ name, size: 1 })).toBeNull();
      expect(ATTACHMENT_ACCEPT).toContain(name.slice(name.lastIndexOf(".")));
    }
  });

  it("explains legacy, unsupported, empty, and oversized files", () => {
    expect(attachmentPreflightError({ name: "legacy.doc", size: 1 })).toContain("legacy Office");
    expect(attachmentPreflightError({ name: "archive.zip", size: 1 })).toContain("not a supported");
    expect(attachmentPreflightError({ name: "empty.pdf", size: 0 })).toContain("between 1 byte");
    expect(attachmentPreflightError({ name: "large.pdf", size: MAX_ATTACHMENT_BYTES + 1 })).toContain("20 MB");
  });
});
