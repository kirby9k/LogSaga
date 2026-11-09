import { useEffect, useRef } from 'react'
import { createRootRoute, Outlet, useLocation } from '@tanstack/react-router'
import { AppLayout } from '@/components/layout/AppLayout'
import { savePosition, restorePosition, resetPosition } from '@/lib/scrollRestoration'

export const Route = createRootRoute({
  component: Root,
})

function Root() {
  const location = useLocation()
  // A stable string key for this location (pathname + search string)
  const locationKey = location.pathname + location.searchStr
  const prevKeyRef = useRef<string>(locationKey)
  // Set to true by popstate (browser back/forward) before React re-renders
  const isPopRef = useRef<boolean>(false)

  // Listen for native back/forward events so we know to restore scroll
  useEffect(() => {
    const onPop = () => { isPopRef.current = true }
    window.addEventListener('popstate', onPop)
    return () => window.removeEventListener('popstate', onPop)
  }, [])

  useEffect(() => {
    if (isPopRef.current) {
      // Back/forward: restore saved scroll for this location
      restorePosition(locationKey)
      isPopRef.current = false
    } else {
      // Forward navigation: start at top
      resetPosition()
    }

    // Cleanup: save this page's scroll position before the next navigation
    const keyAtEffect = locationKey
    return () => {
      savePosition(keyAtEffect)
      prevKeyRef.current = keyAtEffect
    }
  }, [locationKey])

  return (
    <AppLayout>
      <Outlet />
    </AppLayout>
  )
}
