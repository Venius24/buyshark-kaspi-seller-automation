import i18n from 'i18next'
import { initReactI18next } from 'react-i18next'
import ruTranslations from './locales/ru.json'
import kzTranslations from './locales/kz.json'

const resources = {
  ru: ruTranslations,
  kz: kzTranslations,
}

const defaultLang = localStorage.getItem('language') || 'ru'

i18n
  .use(initReactI18next)
  .init({
    resources,
    lng: defaultLang,
    fallbackLng: 'ru',
    interpolation: {
      escapeValue: false,
    },
  })

i18n.on('languageChanged', (lng) => {
  localStorage.setItem('language', lng)
})

export default i18n
