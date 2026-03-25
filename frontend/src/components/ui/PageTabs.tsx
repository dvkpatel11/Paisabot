import { useState, type ReactNode } from 'react'
import { cn } from '@/lib/utils'

export interface Tab {
  id: string
  label: string
  count?: number
  content: ReactNode
}

interface PageTabsProps {
  tabs: Tab[]
  defaultTab?: string
}

export function PageTabs({ tabs, defaultTab }: PageTabsProps) {
  const [active, setActive] = useState(defaultTab ?? tabs[0]?.id ?? '')

  return (
    <div className="flex flex-col flex-1 min-h-0">
      <div className="page-tab-bar">
        {tabs.map((tab) => (
          <button
            key={tab.id}
            className={cn('page-tab', active === tab.id && 'active')}
            onClick={() => setActive(tab.id)}
          >
            {tab.label}
            {tab.count != null && (
              <span className={cn(
                'mono text-[10px] px-1.5 py-px rounded-full',
                active === tab.id ? 'bg-accent/15 text-accent' : 'bg-overlay text-muted'
              )}>
                {tab.count}
              </span>
            )}
          </button>
        ))}
      </div>
      <div className="flex-1 overflow-y-auto p-4">
        {tabs.find((t) => t.id === active)?.content}
      </div>
    </div>
  )
}
