import { createContext, useContext, useCallback } from 'react'
import { useTranslation } from 'react-i18next'

type Language = 'ru' | 'kz'

interface LanguageContextType {
  language: Language
  setLanguage: (lang: Language) => void
  t: (key: string, defaultValue?: string) => string
}

const LanguageContext = createContext<LanguageContextType | undefined>(undefined)

export function LanguageProvider({ children }: { children: React.ReactNode }) {
  const { i18n, t } = useTranslation()

  const setLanguage = useCallback((lang: Language) => {
    i18n.changeLanguage(lang)
  }, [i18n])

  return (
    <LanguageContext.Provider value={{
      language: i18n.language as Language,
      setLanguage,
      t: t as unknown as (key: string, defaultValue?: string) => string,
    }}>
      {children}
    </LanguageContext.Provider>
  )
}

export function useLanguage() {
  const context = useContext(LanguageContext)
  if (!context) {
    throw new Error('useLanguage must be used within LanguageProvider')
  }
  return context
}
