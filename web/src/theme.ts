import { useEffect, useState } from 'react'

export type Theme = 'system' | 'light' | 'dark'

export function useTheme() {
  const [theme, setTheme] = useState<Theme>(() => {
    const stored = globalThis.localStorage?.getItem?.('windcode-theme')
    return stored === 'light' || stored === 'dark' ? stored : 'system'
  })
  useEffect(() => {
    try { localStorage.setItem('windcode-theme', theme) } catch { /* 存储不可用时保持会话内生效 */ }
    const dark = theme === 'dark' || (theme === 'system' && matchMedia('(prefers-color-scheme: dark)').matches)
    document.documentElement.dataset.theme = dark ? 'dark' : 'light'
  }, [theme])
  return [theme, setTheme] as const
}
