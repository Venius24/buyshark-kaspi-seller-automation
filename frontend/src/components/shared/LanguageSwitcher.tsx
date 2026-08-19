import { useLanguage } from '../../context/LanguageContext'

export function LanguageSwitcher() {
  const { language, setLanguage } = useLanguage()

  return (
    <div className="flex gap-2">
      <button
        onClick={() => setLanguage('ru')}
        className={`px-3 py-1 rounded ${language === 'ru' ? 'bg-blue-600 text-white' : 'bg-gray-200'}`}
      >
        РУ
      </button>
      <button
        onClick={() => setLanguage('kz')}
        className={`px-3 py-1 rounded ${language === 'kz' ? 'bg-blue-600 text-white' : 'bg-gray-200'}`}
      >
        КЗ
      </button>
    </div>
  )
}
