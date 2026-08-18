import type React from 'react'
import css from './IconButton.module.css'

export function IconButton(props: React.ButtonHTMLAttributes<HTMLButtonElement> & { label: string }) {
  const { label, children, className, ...rest } = props
  const merged = className ? `${css.iconButton} ${className}` : css.iconButton
  return <button type="button" className={merged} title={label} aria-label={label} {...rest}>{children}</button>
}
