import { StrictMode } from 'react'
import { createRoot } from 'react-dom/client'
import App from './App'
import './styles.css'
import { blockBackspaceNavigation } from './lib/backspace'
import { hydrateListCache } from './lib/listCache'

blockBackspaceNavigation()

document.documentElement.style.setProperty(
  '--app-background-image',
  `url("/bg.webp?v=${Date.now()}")`,
)

// Before the first render, so the library pages can start from the saved lists.
void hydrateListCache().finally(() => {
  createRoot(document.getElementById('root')!).render(<StrictMode><App /></StrictMode>)
})
