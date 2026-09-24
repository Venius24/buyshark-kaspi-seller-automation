import { StrictMode } from 'react'
import { createRoot } from 'react-dom/client'
import { PersistQueryClientProvider } from '@tanstack/react-query-persist-client'
import * as Sentry from '@sentry/react'
import './i18n'
import './index.css'
import './mobile.css'
import App from './App.tsx'
import { LanguageProvider } from './context/LanguageContext'
import { queryClient, queryPersister, CACHE_BUSTER } from './queryClient'

// После деплоя у давно открытой вкладки старых кусков (lazy-виджеты, kz-словарь)
// на сервере уже нет — перезагружаемся на свежий index.html. Гард в sessionStorage
// не даёт уйти в цикл, если кусок недоступен по другой причине.
window.addEventListener('vite:preloadError', (event) => {
  try {
    const last = Number(sessionStorage.getItem('chunk_reload_at') || 0)
    if (Date.now() - last < 60_000) return
    sessionStorage.setItem('chunk_reload_at', String(Date.now()))
  } catch {
    return
  }
  event.preventDefault()
  window.location.reload()
})

const SENTRY_DSN =import.meta.env.VITE_SENTRY_DSN
if (SENTRY_DSN) {
  Sentry.init({
    dsn: SENTRY_DSN,
    environment: import.meta.env.MODE,
    integrations: [Sentry.browserTracingIntegration()],
    tracesSampleRate: 0.1,
  })
}

createRoot(document.getElementById('root')!).render(
  <StrictMode>
    <LanguageProvider>
      <PersistQueryClientProvider
        client={queryClient}
        persistOptions={{ persister: queryPersister, maxAge: 24 * 60 * 60 * 1000, buster: CACHE_BUSTER }}
      >
        <App />
      </PersistQueryClientProvider>
    </LanguageProvider>
  </StrictMode>,
)
