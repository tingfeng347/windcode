import { spawnSync } from "node:child_process";
import { readFileSync } from "node:fs";

const packageMetadata = JSON.parse(
  readFileSync(new URL("../../package.json", import.meta.url), "utf8"),
);

export function runWindcode(args, spawn = spawnSync) {
  const result = spawn(
    "uvx",
    ["--from", `windcode==${packageMetadata.version}`, "windcode", ...args],
    { stdio: "inherit" },
  );

  if (result.error) {
    if (result.error.code === "ENOENT") {
      console.error(
        "windcode: uvx is required. Install uv from https://docs.astral.sh/uv/getting-started/installation/",
      );
    } else {
      console.error(`windcode: failed to start uvx: ${result.error.message}`);
    }
    return 1;
  }

  return result.status ?? 1;
}
