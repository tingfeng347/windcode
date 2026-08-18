import { spawn } from 'node:child_process'

const workspace = process.cwd()
const api = spawn('uv', ['run', 'windcode', 'web', workspace, '--port', '8765', '--no-open'], {
  cwd: workspace,
  stdio: 'inherit',
})
let frontend

let stopping = false
function stop(exitCode = 0) {
  if (stopping) return
  stopping = true
  api.kill('SIGTERM')
  frontend?.kill('SIGTERM')
  process.exit(exitCode)
}

async function waitForApi() {
  for (let attempt = 0; attempt < 50; attempt += 1) {
    if (api.exitCode !== null) stop(api.exitCode || 1)
    try {
      const response = await fetch('http://127.0.0.1:8765/api/v1/workspaces')
      if (response.ok) return
    } catch {
      // The server is still starting.
    }
    await new Promise(resolve => setTimeout(resolve, 100))
  }
  console.error('Windcode API did not start on http://127.0.0.1:8765')
  stop(1)
}

process.on('SIGINT', () => stop())
process.on('SIGTERM', () => stop())
api.on('exit', code => stop(code ?? 1))

await waitForApi()
frontend = spawn('pnpm', ['--dir', 'web', 'dev'], { cwd: workspace, stdio: 'inherit' })
frontend.on('exit', code => stop(code ?? 1))
