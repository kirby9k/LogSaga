import { useEffect, useState } from 'react'
import { Sidebar } from './Sidebar'
import { MobileHeader } from './MobileHeader'
import { GlobalSearch } from '@/components/shared/GlobalSearch'

interface Props {
  children: React.ReactNode
}

export function AppLayout({ children }: Props) {
  const [sidebarOpen, setSidebarOpen] = useState(false)
  const [searchOpen, setSearchOpen] = useState(false)

  // Ctrl+K / Cmd+K opens search
  useEffect(() => {
    function onKeyDown(e: KeyboardEvent) {
      if ((e.ctrlKey || e.metaKey) && e.key === 'k') {
        e.preventDefault()
        setSearchOpen(true)
      }
    }
    window.addEventListener('keydown', onKeyDown)
    return () => window.removeEventListener('keydown', onKeyDown)
  }, [])

  return (
    <div className="flex h-screen bg-background text-foreground overflow-hidden">
      {/* Desktop sidebar */}
      <div className="hidden md:flex md:flex-shrink-0">
        <Sidebar onSearchOpen={() => setSearchOpen(true)} />
      </div>

      {/* Mobile sidebar overlay */}
      {sidebarOpen && (
        <div
          className="fixed inset-0 z-40 md:hidden"
          onClick={() => setSidebarOpen(false)}
        >
          <div className="absolute inset-0 bg-black/60" />
          <div className="relative z-50 h-full" onClick={e => e.stopPropagation()}>
            <Sidebar onClose={() => setSidebarOpen(false)} onSearchOpen={() => { setSidebarOpen(false); setSearchOpen(true) }} />
          </div>
        </div>
      )}

      {/* Main content */}
      <div className="flex flex-col flex-1 min-w-0 overflow-hidden">
        <MobileHeader onMenuClick={() => setSidebarOpen(true)} />
        <main id="main-scroll" className="flex-1 overflow-y-auto">
          {children}
        </main>
      </div>

      {/* Global search overlay */}
      <GlobalSearch open={searchOpen} onClose={() => setSearchOpen(false)} />
    </div>
  )
}
