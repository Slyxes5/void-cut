import { StrictMode } from 'react'
import { createRoot } from 'react-dom/client'
import { LanguageProvider } from './locales'
import './index.css'
import App from './App.tsx'
import { AuthGate } from './auth/AuthGate'
import { installAuthFetch } from './auth/client'
import './auth/auth.css'

installAuthFetch()

createRoot(document.getElementById('root')!).render(
  <StrictMode>
    <LanguageProvider>
      <AuthGate><App /></AuthGate>
    </LanguageProvider>
  </StrictMode>,
)
