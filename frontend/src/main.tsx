import { StrictMode } from 'react'
import { createRoot } from 'react-dom/client'
import App from './App'
import './styles.css'
import { blockBackspaceNavigation } from './lib/backspace'

blockBackspaceNavigation()

document.documentElement.style.setProperty(
  '--app-background-image',
  `url("/bg.webp?v=${Date.now()}")`,
)

createRoot(document.getElementById('root')!).render(<StrictMode><App /></StrictMode>)
