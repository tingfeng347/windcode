#!/usr/bin/env node

import { runWindcode } from "../lib/launcher.js";

process.exitCode = runWindcode(process.argv.slice(2));
