import { createHash } from "node:crypto";
import { createReadStream } from "node:fs";
import { readdir, writeFile } from "node:fs/promises";
import path from "node:path";
import { fileURLToPath } from "node:url";

const scriptDirectory = path.dirname(fileURLToPath(import.meta.url));
const releaseDirectory = path.resolve(scriptDirectory, "../release");
const checksumPath = path.join(releaseDirectory, "SHA256SUMS.txt");
const distributableSuffixes = [".dmg", ".zip", ".exe", ".blockmap", ".whl"];

/** Return distributable files that should be covered by the release checksum manifest. */
async function listReleaseArtifacts() {
  const entries = await readdir(releaseDirectory, { withFileTypes: true });
  return entries
    .filter(
      (entry) => entry.isFile() && distributableSuffixes.some((suffix) => entry.name.endsWith(suffix)),
    )
    .map((entry) => entry.name)
    .sort();
}

/** Hash one release artifact without loading the entire file into memory. */
async function hashArtifact(artifactPath) {
  const hash = createHash("sha256");
  for await (const chunk of createReadStream(artifactPath)) {
    hash.update(chunk);
  }
  return hash.digest("hex");
}

const artifacts = await listReleaseArtifacts();
if (artifacts.length === 0) {
  throw new Error(`No release artifacts found in ${releaseDirectory}`);
}

const lines = [];
for (const artifact of artifacts) {
  const digest = await hashArtifact(path.join(releaseDirectory, artifact));
  lines.push(`${digest}  ${artifact}`);
}

await writeFile(checksumPath, `${lines.join("\n")}\n`, "utf8");
console.log(`Wrote ${path.relative(process.cwd(), checksumPath)} for ${artifacts.length} artifact(s).`);
