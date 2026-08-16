import assert from "node:assert/strict";
import test from "node:test";

import { runWindcode } from "../lib/launcher.js";

test("launches the matching Python package and forwards arguments", () => {
  let invocation;
  const status = runWindcode([".", "--model", "primary"], (...args) => {
    invocation = args;
    return { status: 7 };
  });

  assert.equal(status, 7);
  assert.deepEqual(invocation, [
    "uvx",
    ["--from", "windcode==0.4.2", "windcode", ".", "--model", "primary"],
    { stdio: "inherit" },
  ]);
});

test("returns an error when uvx is unavailable", () => {
  const originalError = console.error;
  let message;
  console.error = (value) => {
    message = value;
  };

  try {
    const status = runWindcode([], () => ({
      error: Object.assign(new Error("not found"), { code: "ENOENT" }),
    }));
    assert.equal(status, 1);
    assert.match(message, /uvx is required/);
  } finally {
    console.error = originalError;
  }
});
