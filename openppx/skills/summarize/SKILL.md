---
name: summarize
description: Extract the main text from web pages and generate concise summaries.
metadata:
  openppx:
    version: 1.0.0
    risk: low
    dependencies:
      executables: [trafilatura]
---

# summarize

Use `trafilatura` to extract the main text from web pages and generate concise summaries.

## Usage

1. **Summarize a web page**
   Provide a URL. The skill uses `trafilatura` to fetch the main content and summarize it.
   Example: `Summarize this page: https://example.com`

2. **Core flow**
   - Run `trafilatura --markdown -u <URL>` to extract content.
   - Use AI to condense the extracted Markdown content.

## Dependency
- `trafilatura`, already installed
