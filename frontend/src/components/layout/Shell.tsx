import { useState, useEffect } from 'react'
import { Outlet } from '@tanstack/react-router'
import { Topbar } from './Topbar'
import { Sidebar } from './Sidebar'
import { StatusBar } from './StatusBar'
import { CommandPalette } from './CommandPalette'
import { AlertsPanel } from './AlertsPanel'
import { useSocketBridge } from '@/hooks/useSocket'
import { useAppStore } from '@/stores/app'

export function Shell() {
  const [commandOpen, setCommandOpen] = useState(false)
  const killSwitches = useAppStore((s) => s.killSwitches)
  const anyKillActive = Object.values(killSwitches).some(Boolean)

  // Connect Socket.IO bridge
  useSocketBridge()

  // Global Ctrl+K
  useEffect(() => {
    function onKey(e: KeyboardEvent) {
      if (e.key === 'k' && (e.metaKey || e.ctrlKey)) {
        e.preventDefault()
        setCommandOpen((v) => !v)
      }
    }
    window.addEventListener('keydown', onKey)
    return () => window.removeEventListener('keydown', onKey)
  }, [])

  return (
    <div className="h-screen overflow-hidden">
      {/* Kill banner */}
      {anyKillActive && (
        <div className="fixed top-0 left-0 right-0 z-[9999] bg-loss text-white mono text-sm font-semibold text-center py-2 flex items-center justify-center gap-3 tracking-wider">
          <span>TRADING HALTED</span>
          <a href="/config" className="text-white underline ml-2">Manage &rarr;</a>
        </div>
      )}

      <Topbar onCommandOpen={() => setCommandOpen(true)} />

      <div className="flex h-screen pt-topbar">
        <Sidebar />
        <main className="flex-1 overflow-y-auto overflow-x-hidden p-5 pb-[calc(theme(spacing.statusbar)+20px)] flex flex-col gap-4">
          <Outlet />
        </main>
      </div>

      <StatusBar />
      <AlertsPanel />
      <CommandPalette open={commandOpen} onClose={() => setCommandOpen(false)} />
    </div>
  )
}
