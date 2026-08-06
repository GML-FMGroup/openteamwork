import { createHash } from "node:crypto";
import { createReadStream } from "node:fs";
import { readFile, readdir, writeFile } from "node:fs/promises";
import path from "node:path";
import { fileURLToPath } from "node:url";

const scriptDirectory = path.dirname(fileURLToPath(import.meta.url));
const releaseDirectory = path.resolve(scriptDirectory, "../release");
const checksumPath = path.join(releaseDirectory, "SHA256SUMS.txt");
const distributableSuffixes = [".dmg", ".zip", ".exe", ".blockmap", ".whl"];

/** Return the Desktop package version used in release artifact filenames. */
async function readReleaseVersion() {
  const packagePath = path.resolve(scriptDirectory, "../package.json");
  const manifest = JSON.parse(await readFile(packagePath, "utf8"));
  if (typeof manifest.version !== "string" || !/^\d+\.\d+\.\d+$/.test(manifest.version)) {
    throw new Error(`Invalid release version in ${packagePath}`);
  }
  return manifest.version;
}

/** Return distributable files that should be covered by the release checksum manifest. */
async function listReleaseArtifacts(version) {
  const entries = await readdir(releaseDirectory, { withFileTypes: true });
  return entries
    .filter(
      (entry) =>
        entry.isFile() &&
        entry.name.includes(version) &&
        distributableSuffixes.some((suffix) => entry.name.endsWith(suffix)),
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

const releaseVersion = await readReleaseVersion();
const artifacts = await listReleaseArtifacts(releaseVersion);
if (artifacts.length === 0) {
  throw new Error(`No v${releaseVersion} release artifacts found in ${releaseDirectory}`);
}

const lines = [];
for (const artifact of artifacts) {
  const digest = await hashArtifact(path.join(releaseDirectory, artifact));
  lines.push(`${digest}  ${artifact}`);
}

await writeFile(checksumPath, `${lines.join("\n")}\n`, "utf8");
console.log(`Wrote ${path.relative(process.cwd(), checksumPath)} for ${artifacts.length} artifact(s).`);
