import { useState } from 'react'
import { useLocation, Link } from '@tanstack/react-router'
import { ChevronDown } from 'lucide-react'
import * as icons from 'lucide-react'
import { cn } from '@/lib/utils'
import { NAV_SECTIONS } from '@/lib/constants'
import { useAppStore } from '@/stores/app'

export function Sidebar() {
  const location = useLocation()
  const alerts = useAppStore((s) => s.alerts)

  return (
    <nav className="w-sidebar min-w-[200px] bg-surface border-r border-border overflow-y-auto py-4 flex flex-col gap-0.5">
      {NAV_SECTIONS.map((section) => (
        <NavSection
          key={section.label}
          label={section.label}
          items={section.items}
          currentPath={location.pathname}
          alertCount={section.label === 'SYSTEM' ? alerts.length : 0}
        />
      ))}
    </nav>
  )
}

function NavSection({ label, items, currentPath, alertCount }: {
  label: string
  items: ReadonlyArray<{ name: string; path: string; icon: string }>
  currentPath: string
  alertCount: number
}) {
  const [open, setOpen] = useState(true)

  return (
    <div className="mb-1">
      <button
        onClick={() => setOpen(!open)}
        className="w-full flex items-center justify-between px-4 pt-4 pb-2 text-2xs font-semibold tracking-[0.1em] text-muted uppercase cursor-pointer select-none hover:text-secondary transition-colors"
      >
        {label}
        <ChevronDown size={10} className={cn('transition-transform', !open && '-rotate-90')} />
      </button>

      {open && (
        <div className="flex flex-col gap-0.5">
          {items.map((item) => {
            const active = currentPath === item.path || currentPath.startsWith(item.path + '/')
            const IconComponent = (icons as Record<string, React.ComponentType<{ size?: number }>>)[item.icon]

            return (
              <Link
                key={item.path}
                to={item.path}
                className={cn(
                  'flex items-center gap-3 py-2 px-4 mx-2 rounded text-sm font-medium no-underline transition-colors relative',
                  active
                    ? 'bg-accent/10 text-accent font-semibold'
                    : 'text-secondary hover:bg-elevated hover:text-primary'
                )}
              >
                {active && <span className="absolute left-0 top-1 bottom-1 w-0.5 bg-accent rounded-r" />}
                {IconComponent && <IconComponent size={14} />}
                <span>{item.name}</span>
                {item.name === 'Alerts' && alertCount > 0 && (
                  <span className="ml-auto px-1.5 py-0 rounded-full text-2xs font-bold bg-loss/10 text-loss mono">{alertCount}</span>
                )}
              </Link>
            )
          })}
        </div>
      )}
    </div>
  )
}
