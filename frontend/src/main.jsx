import { StrictMode } from 'react'
import { createRoot } from 'react-dom/client'
import './index.css'

// Runtime configuration: on Cloud Run the web container writes /config.json from its environment
// (API_URL=https://backend-xxxx.run.app) so the same image works in every environment.
async function loadRuntimeConfig() {
  try {
    const response = await fetch('/config.json', { cache: 'no-store' })
    if (response.ok && (response.headers.get('content-type') || '').includes('json')) {
      window.__APP_CONFIG__ = await response.json()
    }
  } catch {
    /* no runtime config: fall back to VITE_API_URL / local dev */
  }
}

async function start() {
  await loadRuntimeConfig()
  const isAdmin = window.location.pathname === '/admin' || window.location.pathname.startsWith('/admin/')
  // Loaded lazily so the candidate bundle never ships the admin screens (and the reverse).
  const Root = isAdmin
    ? (await import('./admin/AdminApp.jsx')).default
    : (await import('./App.jsx')).default

  createRoot(document.getElementById('root')).render(
    <StrictMode>
      <Root />
    </StrictMode>,
  )
}

start()
