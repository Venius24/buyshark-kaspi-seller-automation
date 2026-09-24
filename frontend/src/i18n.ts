import i18n from 'i18next'
import { initReactI18next } from 'react-i18next'
import ruTranslations from './locales/ru.json'

// ru — fallback, всегда в сборке. Остальные языки подгружаются отдельным
// куском только тем, кто их выбрал.
// Ключи верхнего уровня в locales/*.json — это неймспейсы (useTranslation('tab_overview')).
type LocaleFile = Record<string, Record<string, unknown>>
const lazyLocales: Record<string, () => Promise<{ default: LocaleFile }>> = {
  kz: () => import('./locales/kz.json'),
}

export async function ensureLanguageLoaded(lng: string): Promise<void> {
  const load = lazyLocales[lng]
  if (!load || i18n.hasResourceBundle(lng, 'common')) return
  const { default: namespaces } = await load()
  for (const [ns, bundle] of Object.entries(namespaces)) {
    i18n.addResourceBundle(lng, ns, bundle, true, true)
  }
}

const defaultLang = localStorage.getItem('language') || 'ru'

i18n
  .use(initReactI18next)
  .init({
    resources: { ru: ruTranslations },
    partialBundledLanguages: true,
    lng: defaultLang,
    fallbackLng: 'ru',
    interpolation: {
      escapeValue: false,
      prefix: '%',
      suffix: '%',
    },
  })

i18n.on('languageChanged', (lng) => {
  localStorage.setItem('language', lng)
})

// Сохранённый не-ru язык: пока кусок грузится, видны ru-строки (fallback), потом
// повторный changeLanguage перерисовывает интерфейс уже на нужном языке.
if (lazyLocales[defaultLang]) {
  ensureLanguageLoaded(defaultLang)
    .then(() => i18n.changeLanguage(defaultLang))
    .catch(() => {})
}

export default i18n
