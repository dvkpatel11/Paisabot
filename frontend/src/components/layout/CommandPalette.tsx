import { useEffect, useRef, useState } from 'react'
import { useNavigate } from '@tanstack/react-router'
import { Search } from 'lucide-react'
import * as allIcons from 'lucide-react'
import { cn } from '@/lib/utils'
import { NAV_SECTIONS } from '@/lib/constants'

interface CommandPaletteProps {
  open: boolean
  onClose: () => void
}

interface CommandItem {
  section: string
  name: string
  path: string
  icon: string
}

const ALL_ITEMS: CommandItem[] = NAV_SECTIONS.flatMap((s) =>
  s.items.map((item) => ({ section: s.label, name: item.name, path: item.path, icon: item.icon }))
)

export function CommandPalette({ open, onClose }: CommandPaletteProps) {
  const [query, setQuery] = useState('')
  const [selectedIdx, setSelectedIdx] = useState(0)
  const inputRef = useRef<HTMLInputElement>(null)
  const navigate = useNavigate()

  const filtered = query
    ? ALL_ITEMS.filter((item) =>
        item.name.toLowerCase().includes(query.toLowerCase()) ||
        item.section.toLowerCase().includes(query.toLowerCase())
      )
    : ALL_ITEMS

  // Group by section
  const grouped = new Map<string, CommandItem[]>()
  for (const item of filtered) {
    const existing = grouped.get(item.section) ?? []
    existing.push(item)
    grouped.set(item.section, existing)
  }

  useEffect(() => {
    if (open) {
      setQuery('')
      setSelectedIdx(0)
      setTimeout(() => inputRef.current?.focus(), 50)
    }
  }, [open])

  useEffect(() => { setSelectedIdx(0) }, [query])

  function go(path: string) {
    navigate({ to: path })
    onClose()
  }

  function handleKey(e: React.KeyboardEvent) {
    if (e.key === 'ArrowDown') { e.preventDefault(); setSelectedIdx((i) => (i + 1) % filtered.length) }
    else if (e.key === 'ArrowUp') { e.preventDefault(); setSelectedIdx((i) => (i - 1 + filtered.length) % filtered.length) }
    else if (e.key === 'Enter') { e.preventDefault(); const item = filtered[selectedIdx]; if (item) go(item.path) }
    else if (e.key === 'Escape') { onClose() }
  }

  if (!open) return null

  let flatIdx = 0

  return (
    <div className="fixed inset-0 z-[500] bg-black/60 flex items-start justify-center pt-[15vh]" onClick={onClose}>
      <div className="w-[520px] bg-surface border border-border rounded-lg shadow-[0_8px_32px_rgba(0,0,0,0.7)] overflow-hidden" onClick={(e) => e.stopPropagation()}>
        <div className="flex items-center gap-3 px-4 py-3 border-b border-border">
          <Search size={16} className="text-muted" />
          <input
            ref={inputRef}
            type="text"
            className="flex-1 bg-transparent border-none outline-none text-primary mono text-md placeholder:text-muted"
            placeholder="Search pages, symbols, actions..."
            value={query}
            onChange={(e) => setQuery(e.target.value)}
            onKeyDown={handleKey}
          />
        </div>
        <div className="max-h-[320px] overflow-y-auto">
          {Array.from(grouped.entries()).map(([section, items]) => (
            <div key={section}>
              <div className="px-4 py-2 text-[10px] font-bold text-muted uppercase tracking-[0.1em]">{section}</div>
              {items.map((item) => {
                const thisIdx = flatIdx++
                const IconComponent = (allIcons as Record<string, React.ComponentType<{ size?: number }>>)[item.icon]
                return (
                  <div
                    key={item.path}
                    className={cn(
                      'flex items-center gap-3 px-4 py-2 cursor-pointer text-sm text-secondary transition-colors',
                      thisIdx === selectedIdx && 'bg-elevated text-primary'
                    )}
                    onClick={() => go(item.path)}
                    onMouseEnter={() => setSelectedIdx(thisIdx)}
                  >
                    {IconComponent ? <IconComponent size={14} /> : <span className="w-3.5" />}
                    <span>{item.name}</span>
                  </div>
                )
              })}
            </div>
          ))}
        </div>
      </div>
    </div>
  )
}
