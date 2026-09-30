import React from 'react'
import ReactDOM from 'react-dom/client'
import { BrowserRouter } from 'react-router-dom'
import '@fontsource-variable/inter'
import '@fontsource-variable/heebo'
import { initLanguage } from './i18n'
import './lib/theme'
import App from './App'
import { StoreProvider } from './lib/store'
import './index.css'

// השפה נקבעת אוטומטית (ראו i18n/index.ts) לפני הציור הראשון, כדי שלא
// יהבהב ממשק בשפה אחת ויתהפך לשנייה
void initLanguage().finally(() => ReactDOM.createRoot(document.getElementById('root')!).render(
  <React.StrictMode>
    <BrowserRouter>
      <StoreProvider>
        <App />
      </StoreProvider>
    </BrowserRouter>
  </React.StrictMode>,
))
