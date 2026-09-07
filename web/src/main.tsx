import { StrictMode } from 'react'
import { createRoot } from 'react-dom/client'

import App from './App'
import { initI18n } from './lib/i18n'
import { initTheme } from './lib/theme'
import './styles/index.css'

initTheme()
initI18n()

const container = document.getElementById('root')
if (!container) throw new Error('missing #root element')

createRoot(container).render(
  <StrictMode>
    <App />
  </StrictMode>,
)
